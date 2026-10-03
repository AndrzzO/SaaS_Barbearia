"""
Testes dedicados de Rate Limiting por IP para login (Passo 15).
Guild Tech Support.
"""

import json
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from fila.models import Barbeiro, RegistroTentativaLoginIP
from fila.rate_limit import RateLimitExcedido, registrar_e_validar_tentativa_login

User = get_user_model()


class TestRateLimitLogin(TestCase):
    """Testa os limites de tentativas, contagem atômica no SQLite e bloqueios temporários."""

    def setUp(self):
        self.client = Client()
        self.user_barbeiro = User.objects.create_user(
            username="barbeiro_teste",
            password="senha_correta_123"
        )
        self.barbeiro = Barbeiro.objects.create(
            usuario=self.user_barbeiro,
            nome_exibicao="Barbeiro RateLimit",
            ativo=True,
            online=True
        )

    @override_settings(
        RATE_LIMIT_LOGIN_MAX_TENTATIVAS=5,
        RATE_LIMIT_LOGIN_JANELA_MINUTOS=10,
        RATE_LIMIT_LOGIN_BLOQUEIO_MINUTOS=30
    )
    def test_limite_tentativas_e_bloqueio_429(self):
        url = reverse('barbeiro_login')
        payload = {'username': 'barbeiro_teste', 'senha': 'senha_errada'}

        # Com limite configurado para 5:
        # As primeiras 5 tentativas são processadas normalmente (retornam 401 por credenciais erradas)
        for i in range(1, 6):
            resp = self.client.post(
                url,
                data=json.dumps(payload),
                content_type='application/json',
                REMOTE_ADDR='192.168.1.100'
            )
            self.assertEqual(resp.status_code, 401, f"Tentativa {i} deveria ter sido processada.")

        # A 6ª tentativa e seguintes devem ser bloqueadas com HTTP 429
        resp_bloqueado = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='192.168.1.100'
        )
        self.assertEqual(resp_bloqueado.status_code, 429)
        self.assertIn('Retry-After', resp_bloqueado)
        self.assertGreater(int(resp_bloqueado['Retry-After']), 0)

        # Tentativas bloqueadas NÃO prolongam o tempo de bloqueio
        registro = RegistroTentativaLoginIP.objects.get(ip='192.168.1.100', escopo='barbeiro')
        bloqueado_ate_original = registro.bloqueado_ate

        # Faz mais uma tentativa bloqueada
        self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='192.168.1.100'
        )
        registro.refresh_from_db()
        self.assertEqual(registro.bloqueado_ate, bloqueado_ate_original)

    @override_settings(
        RATE_LIMIT_LOGIN_MAX_TENTATIVAS=3,
        RATE_LIMIT_LOGIN_JANELA_MINUTOS=10,
        RATE_LIMIT_LOGIN_BLOQUEIO_MINUTOS=30
    )
    def test_bloqueio_expira_e_permite_novas_tentativas(self):
        ip = '192.168.1.105'
        # Atinge o limite de 3 tentativas
        for _ in range(3):
            registrar_e_validar_tentativa_login(ip, escopo='barbeiro')

        # A próxima tentativa direta levanta RateLimitExcedido
        with self.assertRaises(RateLimitExcedido):
            registrar_e_validar_tentativa_login(ip, escopo='barbeiro')

        # Simula expiração do bloqueio de 30 minutos
        registro = RegistroTentativaLoginIP.objects.get(ip=ip, escopo='barbeiro')
        registro.bloqueado_ate = timezone.now() - timedelta(minutes=1)
        registro.save(update_fields=['bloqueado_ate'])

        # Agora, a tentativa é permitida novamente e a contagem reinicia
        registrar_e_validar_tentativa_login(ip, escopo='barbeiro')
        registro.refresh_from_db()
        self.assertEqual(registro.tentativas, 1)
        self.assertIsNone(registro.bloqueado_ate)

    def test_rate_limit_nao_afeta_outras_rotas(self):
        # Bloqueia o IP no login
        registro = RegistroTentativaLoginIP.objects.create(
            ip='192.168.1.200',
            escopo='barbeiro',
            tentativas=30,
            primeira_tentativa_em=timezone.now(),
            ultima_tentativa_em=timezone.now(),
            bloqueado_ate=timezone.now() + timedelta(minutes=30)
        )

        # O mesmo IP pode navegar normalmente na home pública
        url_home = reverse('publico_cadeiras')
        resp_home = self.client.get(url_home, REMOTE_ADDR='192.168.1.200')
        self.assertEqual(resp_home.status_code, 200)

    @override_settings(
        TRUSTED_PROXY_IPS=['10.0.0.1'],
        RATE_LIMIT_LOGIN_MAX_TENTATIVAS=2
    )
    def test_identificacao_ip_proxy_confiavel(self):
        url = reverse('barbeiro_login')
        payload = {'username': 'invalido', 'senha': 'errada'}

        # Conexão vindo do proxy confiável 10.0.0.1 com header X-Forwarded-For
        for _ in range(2):
            self.client.post(
                url,
                data=json.dumps(payload),
                content_type='application/json',
                REMOTE_ADDR='10.0.0.1',
                HTTP_X_FORWARDED_FOR='203.0.113.195, 10.0.0.1'
            )

        # O IP registrado no modelo deve ser o IP do cliente (203.0.113.195), e não o proxy
        self.assertTrue(RegistroTentativaLoginIP.objects.filter(ip='203.0.113.195').exists())
