"""
Mecanismo de controle de taxa (Rate Limiting) persistente no SQLite para autenticação.
Guild Tech Support.
"""

from datetime import timedelta
import math
from django.conf import settings
from django.db import transaction, OperationalError
from django.utils import timezone
from fila.models import RegistroTentativaLoginIP


class RateLimitExcedido(Exception):
    """Exceção levantada quando um IP está bloqueado temporariamente por excesso de tentativas."""
    def __init__(self, segundos_restantes: int):
        self.segundos_restantes = max(1, int(segundos_restantes))
        super().__init__(f"Limite de tentativas excedido. Tente novamente em {self.segundos_restantes} segundos.")


def obter_ip_cliente(request) -> str:
    """
    Obtém o IP real do cliente.
    Apenas considera X-Forwarded-For se a conexão vier de um proxy confiável
    explicitamente configurado em settings.TRUSTED_PROXY_IPS.
    """
    remote_addr = request.META.get('REMOTE_ADDR', '127.0.0.1')
    trusted_proxies = getattr(settings, 'TRUSTED_PROXY_IPS', [])

    if remote_addr in trusted_proxies:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            # Pega o primeiro IP da cadeia de proxies
            ips = [ip.strip() for ip in x_forwarded_for.split(',')]
            if ips and ips[0]:
                return ips[0]

    return remote_addr


def registrar_e_validar_tentativa_login(ip: str, escopo: str = 'barbeiro') -> None:
    """
    Valida e registra uma tentativa de login (POST) para um IP em um determinado escopo.
    Regras estritas (Passo 15):
    - Janela: RATE_LIMIT_LOGIN_JANELA_MINUTOS (padrão 10 min)
    - Limite: RATE_LIMIT_LOGIN_MAX_TENTATIVAS (padrão 30 tentativas)
    - Bloqueio: RATE_LIMIT_LOGIN_BLOQUEIO_MINUTOS (padrão 30 min)
    - As primeiras 30 tentativas são processadas.
    - A 30ª tentativa ativa o bloqueio para tentativas subsequentes.
    - Tentativas durante o bloqueio NÃO prolongam o tempo de bloqueio.
    - Se a janela expirou sem atingir o limite, reinicia a contagem.
    """
    janela_minutos = getattr(settings, 'RATE_LIMIT_LOGIN_JANELA_MINUTOS', 10)
    limite_tentativas = getattr(settings, 'RATE_LIMIT_LOGIN_MAX_TENTATIVAS', 30)
    bloqueio_minutos = getattr(settings, 'RATE_LIMIT_LOGIN_BLOQUEIO_MINUTOS', 30)

    agora = timezone.now()
    janela_delta = timedelta(minutes=janela_minutos)
    bloqueio_delta = timedelta(minutes=bloqueio_minutos)

    with transaction.atomic():
        registro, criado = RegistroTentativaLoginIP.objects.get_or_create(
            ip=ip,
            escopo=escopo,
            defaults={
                'tentativas': 1,
                'primeira_tentativa_em': agora,
                'ultima_tentativa_em': agora,
                'bloqueado_ate': None,
            }
        )

        if criado:
            # Primeira tentativa: permitida
            return

        # Verifica se está atualmente bloqueado
        if registro.bloqueado_ate and registro.bloqueado_ate > agora:
            segundos_restantes = math.ceil((registro.bloqueado_ate - agora).total_seconds())
            # Requisições durante o bloqueio não prolongam o bloqueio!
            raise RateLimitExcedido(segundos_restantes)

        # Se o bloqueio anterior já expirou, reseta a janela
        if registro.bloqueado_ate and registro.bloqueado_ate <= agora:
            registro.tentativas = 1
            registro.primeira_tentativa_em = agora
            registro.ultima_tentativa_em = agora
            registro.bloqueado_ate = None
            registro.save(update_fields=['tentativas', 'primeira_tentativa_em', 'ultima_tentativa_em', 'bloqueado_ate'])
            return

        # Verifica se a janela de tempo corrente já expirou
        if agora - registro.primeira_tentativa_em > janela_delta:
            # Nova janela iniciada
            registro.tentativas = 1
            registro.primeira_tentativa_em = agora
            registro.ultima_tentativa_em = agora
            registro.bloqueado_ate = None
            registro.save(update_fields=['tentativas', 'primeira_tentativa_em', 'ultima_tentativa_em', 'bloqueado_ate'])
            return

        # Janela ativa e não bloqueado: incrementa tentativa
        registro.tentativas += 1
        registro.ultima_tentativa_em = agora

        # A 30ª tentativa ativa o bloqueio para tentativas subsequentes
        if registro.tentativas >= limite_tentativas:
            registro.bloqueado_ate = agora + bloqueio_delta

        registro.save(update_fields=['tentativas', 'ultima_tentativa_em', 'bloqueado_ate'])
