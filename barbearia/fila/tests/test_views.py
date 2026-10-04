"""
Testes de integração de endpoints da API (contratos JSON, cookies e segurança).
Guild Tech Support.
"""

import json
from datetime import date, timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from fila.models import Barbeiro, ParticipacaoFila, StatusParticipacao
from fila.services import entrar_na_fila, iniciar_atendimento

User = get_user_model()


class TestEndpointsAPI(TestCase):
    """Testa todos os endpoints da API pública, do cliente, do barbeiro e do proprietário."""

    def setUp(self):
        preparar_tenant()
        self.client = Client()

        # Cria proprietário (superusuário)
        self.proprietario_user = criar_dono(
            username="proprietario",
            password="senha_proprietario_123"
        )

        # Cria barbeiro 1
        self.barbeiro1_user = User.objects.create_user(
            username="barbeiro1",
            password="senha_barbeiro_123"
        )
        self.barbeiro1 = Barbeiro.objects.create(
            usuario=self.barbeiro1_user,
            nome_exibicao="Barbeiro Carlos",
            ativo=True,
            online=True,
            proxima_ordem_fila=1
        , barbearia_id=TENANT_ID)

        # Cria barbeiro 2 (offline)
        self.barbeiro2_user = User.objects.create_user(
            username="barbeiro2",
            password="senha_barbeiro_123"
        )
        self.barbeiro2 = Barbeiro.objects.create(
            usuario=self.barbeiro2_user,
            nome_exibicao="Barbeiro Marcos",
            ativo=True,
            online=False,
            proxima_ordem_fila=1
        , barbearia_id=TENANT_ID)

    # --------------------------------------------------------------------------
    # 1. PÚBLICO
    # --------------------------------------------------------------------------

    def test_home_publica_cadeiras(self):
        url = reverse('publico_cadeiras', kwargs={'tenant_slug': 'teste'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertIn('cadeiras', dados)
        self.assertEqual(len(dados['cadeiras']), 2)

        # Verifica que dados pessoais NÃO estão presentes na resposta pública
        json_str = resp.content.decode('utf-8')
        self.assertNotIn("telefone", json_str)
        self.assertNotIn("hash_token_acesso", json_str)
        self.assertNotIn("data_nascimento", json_str)

    def test_entrar_na_fila_sucesso(self):
        url = reverse('publico_entrar_fila', kwargs={'tenant_slug': 'teste', 'barbeiro_id': self.barbeiro1.id})
        payload = {
            'primeiro_nome': 'Gabriel',
            'sobrenome': 'Monteiro',
            'telefone': '(11) 98765-4321',
            'data_nascimento': '1996-05-12'
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        dados = resp.json()
        self.assertIn('identificador_publico', dados)
        self.assertEqual(dados['ordem_fila'], 1)
        self.assertEqual(dados['status'], 'CHAMADO')  # Cadeira livre chama imediatamente!

        # Verifica cookie HttpOnly
        self.assertIn(settings.FILA_COOKIE_NOME, resp.cookies)
        cookie = resp.cookies[settings.FILA_COOKIE_NOME]
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')

    def test_entrar_na_fila_validacao_invalida(self):
        url = reverse('publico_entrar_fila', kwargs={'tenant_slug': 'teste', 'barbeiro_id': self.barbeiro1.id})
        # Telefone inválido
        payload = {
            'primeiro_nome': 'Gabriel',
            'sobrenome': 'Monteiro',
            'telefone': '123',
            'data_nascimento': '1996-05-12'
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('erro', resp.json())

    def test_entrar_na_fila_cadeira_offline(self):
        url = reverse('publico_entrar_fila', kwargs={'tenant_slug': 'teste', 'barbeiro_id': self.barbeiro2.id})
        payload = {
            'primeiro_nome': 'Gabriel',
            'sobrenome': 'Monteiro',
            'telefone': '(11) 98765-4321',
            'data_nascimento': '1996-05-12'
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 409)

    # --------------------------------------------------------------------------
    # 2. CLIENTE TEMPORÁRIO
    # --------------------------------------------------------------------------

    def test_acompanhamento_cliente_contrato_alerta_sonoro(self):
        # Entra na fila para gerar o cookie
        url_entrar = reverse('publico_entrar_fila', kwargs={'tenant_slug': 'teste', 'barbeiro_id': self.barbeiro1.id})
        payload = {
            'primeiro_nome': 'Gabriel',
            'sobrenome': 'Monteiro',
            'telefone': '(11) 98765-4321',
            'data_nascimento': '1996-05-12'
        }
        resp_entrar = self.client.post(url_entrar, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp_entrar.status_code, 201)

        # Consulta acompanhamento usando o cookie definido
        url_acomp = reverse('cliente_acompanhamento', kwargs={'tenant_slug': 'teste'})
        resp_acomp = self.client.get(url_acomp)
        self.assertEqual(resp_acomp.status_code, 200)
        self.assertIn('no-store', resp_acomp['Cache-Control'])

        dados = resp_acomp.json()
        self.assertEqual(dados['status'], 'CHAMADO')
        self.assertIn('identificador_chamada', dados)
        self.assertIn('chamada_iniciada_em', dados)
        self.assertIn('chamada_expira_em', dados)
        self.assertIn('horario_servidor', dados)
        self.assertGreaterEqual(dados['tempo_restante_segundos'], 0)
        self.assertLessEqual(dados['tempo_restante_segundos'], 30)

    def test_acompanhamento_apos_exclusao_limpa_cookie(self):
        url_entrar = reverse('publico_entrar_fila', kwargs={'tenant_slug': 'teste', 'barbeiro_id': self.barbeiro1.id})
        payload = {
            'primeiro_nome': 'Gabriel',
            'sobrenome': 'Monteiro',
            'telefone': '(11) 98765-4321',
            'data_nascimento': '1996-05-12'
        }
        self.client.post(url_entrar, data=json.dumps(payload), content_type='application/json')

        # Exclui a participação no banco
        ParticipacaoFila.objects.filter(telefone='11987654321').delete()

        # Próxima consulta deve retornar ENCERRADO e limpar cookie
        url_acomp = reverse('cliente_acompanhamento', kwargs={'tenant_slug': 'teste'})
        resp_acomp = self.client.get(url_acomp)
        self.assertEqual(resp_acomp.status_code, 200)
        self.assertEqual(resp_acomp.json()['status'], 'ENCERRADO')
        # Cookie deve ser expirado/limpo
        self.assertEqual(resp_acomp.cookies[settings.FILA_COOKIE_NOME].value, '')

    def test_cliente_sair_da_fila(self):
        url_entrar = reverse('publico_entrar_fila', kwargs={'tenant_slug': 'teste', 'barbeiro_id': self.barbeiro1.id})
        payload = {
            'primeiro_nome': 'Gabriel',
            'sobrenome': 'Monteiro',
            'telefone': '(11) 98765-4321',
            'data_nascimento': '1996-05-12'
        }
        self.client.post(url_entrar, data=json.dumps(payload), content_type='application/json')

        url_sair = reverse('cliente_sair_fila', kwargs={'tenant_slug': 'teste'})
        resp_sair = self.client.post(url_sair)
        self.assertEqual(resp_sair.status_code, 200)
        self.assertFalse(ParticipacaoFila.objects.filter(telefone='11987654321').exists())

    # --------------------------------------------------------------------------
    # 3. BARBEIRO
    # --------------------------------------------------------------------------

    def test_barbeiro_login_sucesso_e_logout(self):
        url_login = reverse('barbeiro_login', kwargs={'tenant_slug': 'teste'})
        payload = {'username': 'barbeiro1', 'senha': 'senha_barbeiro_123'}
        resp = self.client.post(url_login, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['barbeiro']['id'], self.barbeiro1.id)

        # Painel operacional acessível após login
        url_painel = reverse('barbeiro_painel', kwargs={'tenant_slug': 'teste'})
        resp_painel = self.client.get(url_painel)
        self.assertEqual(resp_painel.status_code, 200)

        # Logout
        url_logout = reverse('barbeiro_logout', kwargs={'tenant_slug': 'teste'})
        resp_logout = self.client.post(url_logout)
        self.assertEqual(resp_logout.status_code, 200)

        # Painel agora bloqueia
        resp_painel_pos = self.client.get(url_painel)
        self.assertEqual(resp_painel_pos.status_code, 401)

    def test_barbeiro_iniciar_e_finalizar_atendimento(self):
        # Cliente entra na fila e é chamado
        p1, token1, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Lucas",
            sobrenome_raw="Mendes",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1995, 4, 10)
        , barbearia_id=TENANT_ID)
        self.assertEqual(p1.status, StatusParticipacao.CHAMADO)

        # Loga como barbeiro
        self.client.login(username='barbeiro1', password='senha_barbeiro_123')

        # Inicia atendimento com identificador de chamada correto
        url_iniciar = reverse('barbeiro_iniciar_atendimento', kwargs={'tenant_slug': 'teste', 'participacao_id': p1.id})
        payload_iniciar = {'identificador_chamada': str(p1.identificador_chamada)}
        resp_iniciar = self.client.post(url_iniciar, data=json.dumps(payload_iniciar), content_type='application/json')
        self.assertEqual(resp_iniciar.status_code, 200)
        self.assertEqual(resp_iniciar.json()['status'], 'EM_ATENDIMENTO')

        p1.refresh_from_db()
        self.assertEqual(p1.status, StatusParticipacao.EM_ATENDIMENTO)

        # Finaliza o corte
        url_finalizar = reverse('barbeiro_finalizar_corte', kwargs={'tenant_slug': 'teste', 'participacao_id': p1.id})
        resp_finalizar = self.client.post(url_finalizar)
        self.assertEqual(resp_finalizar.status_code, 200)

        # Participação excluída definitivamente
        self.assertFalse(ParticipacaoFila.objects.filter(pk=p1.pk).exists())

    # --------------------------------------------------------------------------
    # 4. PROPRIETÁRIO
    # --------------------------------------------------------------------------

    def test_proprietario_gerenciar_barbeiros(self):
        self.client.login(username='proprietario', password='senha_proprietario_123')

        # Visão geral
        url_visao = reverse('proprietario_visao_geral', kwargs={'tenant_slug': 'teste'})
        resp_visao = self.client.get(url_visao)
        self.assertEqual(resp_visao.status_code, 200)
        self.assertEqual(resp_visao.json()['estatisticas']['total_cadeiras'], 2)

        # Cadastrar novo barbeiro
        url_cadastrar = reverse('proprietario_cadastrar_barbeiro', kwargs={'tenant_slug': 'teste'})
        payload_cadastrar = {
            'username': 'novobarbeiro',
            'senha': 'senha_novo_123',
            'nome_exibicao': 'Barbeiro Thiago'
        }
        resp_cadastrar = self.client.post(url_cadastrar, data=json.dumps(payload_cadastrar), content_type='application/json')
        self.assertEqual(resp_cadastrar.status_code, 201)
        novo_id = resp_cadastrar.json()['barbeiro_id']

        # Editar nome de exibição
        url_editar = reverse('proprietario_editar_barbeiro', kwargs={'tenant_slug': 'teste', 'barbeiro_id': novo_id})
        payload_editar = {'nome_exibicao': 'Barbeiro Thiago Silva'}
        resp_editar = self.client.post(url_editar, data=json.dumps(payload_editar), content_type='application/json')
        self.assertEqual(resp_editar.status_code, 200)

        # Redefinir senha
        url_senha = reverse('proprietario_redefinir_senha', kwargs={'tenant_slug': 'teste', 'barbeiro_id': novo_id})
        payload_senha = {'nova_senha': 'nova_senha_segura_999'}
        resp_senha = self.client.post(url_senha, data=json.dumps(payload_senha), content_type='application/json')
        self.assertEqual(resp_senha.status_code, 200)

        # Desativar barbeiro
        url_status = reverse('proprietario_status_barbeiro', kwargs={'tenant_slug': 'teste', 'barbeiro_id': novo_id})
        payload_status = {'ativo': False}
        resp_status = self.client.post(url_status, data=json.dumps(payload_status), content_type='application/json')
        self.assertEqual(resp_status.status_code, 200)
        self.assertFalse(resp_status.json()['ativo'])

    def test_isolamento_barbeiro_bloqueado_na_gestao(self):
        # Barbeiro comum autenticado não pode acessar rotas de proprietário
        self.client.login(username='barbeiro1', password='senha_barbeiro_123')
        url_visao = reverse('proprietario_visao_geral', kwargs={'tenant_slug': 'teste'})
        resp = self.client.get(url_visao)
        self.assertEqual(resp.status_code, 403)

from fila.tests.tenant_fixture import TENANT_ID, preparar_tenant, criar_dono
