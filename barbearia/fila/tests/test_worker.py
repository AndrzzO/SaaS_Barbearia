"""
Testes dos comandos de supervisão e manutenção de registros (Passo 13, 19 e 20).
Guild Tech Support.
"""

from datetime import date, timedelta
from io import StringIO
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from fila.models import Barbeiro, ParticipacaoFila, StatusParticipacao, RegistroTentativaLoginIP
from fila.services import entrar_na_fila, processar_filas_servico

User = get_user_model()


class TestWorkerEComandos(TestCase):
    """Testa o comando processar_filas e limpar_registros_seguranca."""

    def setUp(self):
        preparar_tenant()
        self.user_barbeiro = User.objects.create_user(
            username="barbeiro_worker",
            password="senha_worker_123"
        )
        self.barbeiro = Barbeiro.objects.create(
            usuario=self.user_barbeiro,
            nome_exibicao="Barbeiro Worker",
            ativo=True,
            online=True,
            proxima_ordem_fila=1
        , barbearia_id=TENANT_ID)

    def test_comando_processar_filas_once(self):
        # Cria participante chamado com prazo vencido
        p1, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro.id,
            primeiro_nome_raw="Carlos",
            sobrenome_raw="Mendes",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1996, 5, 10)
        , barbearia_id=TENANT_ID)
        self.assertEqual(p1.status, StatusParticipacao.CHAMADO)

        p1.chamada_iniciada_em = timezone.now() - timedelta(seconds=40)
        p1.chamada_expira_em = timezone.now() - timedelta(seconds=10)
        p1.save(update_fields=['chamada_iniciada_em', 'chamada_expira_em'])

        # Executa comando via call_command com flag --once
        out = StringIO()
        call_command('processar_filas', '--once', stdout=out)

        p1.refresh_from_db()
        self.assertEqual(p1.status, StatusParticipacao.AGUARDANDO)
        self.assertTrue(p1.aguardando_ultrapassagem)
        self.assertEqual(p1.ultrapassagens_por_falta, 0)  # Sem faltas fabricadas além da expiração

        # Segunda execução imediata não deve processar nada repetidamente (idempotente)
        out2 = StringIO()
        call_command('processar_filas', '--once', stdout=out2)
        p1.refresh_from_db()
        self.assertEqual(p1.status, StatusParticipacao.AGUARDANDO)

    def test_reinicio_worker_nao_fabrica_faltas_retroativas(self):
        """Se o worker ficou desligado por um longo período, apenas expira a chamada vigente uma vez."""
        p1, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro.id,
            primeiro_nome_raw="Carlos",
            sobrenome_raw="Mendes",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1996, 5, 10)
        , barbearia_id=TENANT_ID)
        # Chamada que expirou há 3 horas
        p1.chamada_iniciada_em = timezone.now() - timedelta(hours=3, seconds=30)
        p1.chamada_expira_em = timezone.now() - timedelta(hours=3)
        p1.save(update_fields=['chamada_iniciada_em', 'chamada_expira_em'])

        # Worker reinicia e executa
        processar_filas_servico()

        p1.refresh_from_db()
        self.assertEqual(p1.status, StatusParticipacao.AGUARDANDO)
        self.assertTrue(p1.aguardando_ultrapassagem)
        # Não acumulou faltas repetidas durante o tempo que o worker esteve fora!
        self.assertEqual(p1.ultrapassagens_por_falta, 0)

    def test_comando_limpar_registros_seguranca(self):
        agora = timezone.now()
        # 1. Registro expirado antigo (fora da janela de 10 min)
        r1 = RegistroTentativaLoginIP.objects.create(
            ip='192.168.1.1',
            escopo='barbeiro',
            tentativas=5,
            primeira_tentativa_em=agora - timedelta(minutes=20),
            ultima_tentativa_em=agora - timedelta(minutes=15),
            bloqueado_ate=None
        )
        # 2. Registro com bloqueio expirado no passado
        r2 = RegistroTentativaLoginIP.objects.create(
            ip='192.168.1.2',
            escopo='barbeiro',
            tentativas=30,
            primeira_tentativa_em=agora - timedelta(minutes=45),
            ultima_tentativa_em=agora - timedelta(minutes=35),
            bloqueado_ate=agora - timedelta(minutes=5)
        )
        # 3. Registro com bloqueio VIGENTE (NÃO PODE SER REMOVIDO)
        r3 = RegistroTentativaLoginIP.objects.create(
            ip='192.168.1.3',
            escopo='barbeiro',
            tentativas=30,
            primeira_tentativa_em=agora - timedelta(minutes=5),
            ultima_tentativa_em=agora,
            bloqueado_ate=agora + timedelta(minutes=25)
        )

        out = StringIO()
        call_command('limpar_registros_seguranca', stdout=out)

        self.assertFalse(RegistroTentativaLoginIP.objects.filter(pk=r1.pk).exists())
        self.assertFalse(RegistroTentativaLoginIP.objects.filter(pk=r2.pk).exists())
        # Bloqueio vigente foi preservado!
        self.assertTrue(RegistroTentativaLoginIP.objects.filter(pk=r3.pk).exists())

from fila.tests.tenant_fixture import TENANT_ID, preparar_tenant, criar_dono
