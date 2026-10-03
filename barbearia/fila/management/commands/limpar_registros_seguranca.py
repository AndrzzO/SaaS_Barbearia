"""
Comando de gerenciamento para limpeza de registros técnicos de taxa de login expirados.
Guild Tech Support (Passo 19).
Remove registros obsoletos sem remover bloqueios atualmente vigentes.
"""

from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from fila.models import RegistroTentativaLoginIP


class Command(BaseCommand):
    help = "Remove registros técnicos de taxa de login que já expiraram, preservando bloqueios vigentes."

    def handle(self, *args, **options):
        janela_minutos = getattr(settings, 'RATE_LIMIT_LOGIN_JANELA_MINUTOS', 10)
        agora = timezone.now()
        limite_janela = agora - timedelta(minutes=janela_minutos)

        # Critério de expiração:
        # 1. Registro bloqueado cuja data de desbloqueio já passou no passado (bloqueado_ate < agora)
        # 2. Registro não bloqueado cuja última tentativa é anterior à janela de rate limit
        criterio_expirado = (
            (Q(bloqueado_ate__isnull=False) & Q(bloqueado_ate__lt=agora))
            | (Q(bloqueado_ate__isnull=True) & Q(ultima_tentativa_em__lt=limite_janela))
        )

        with transaction.atomic():
            removidos, _ = RegistroTentativaLoginIP.objects.filter(criterio_expirado).delete()

        self.stdout.write(self.style.SUCCESS(
            f"Limpeza de segurança concluída. {removidos} registro(s) técnico(s) expirado(s) removido(s)."
        ))
