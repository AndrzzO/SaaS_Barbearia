import hashlib
import hmac
import json
import time
import stripe
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.utils import timezone
from allauth.socialaccount.models import SocialAccount, SocialLogin, SocialApp
from allauth.socialaccount.providers.google.provider import GoogleProvider
from allauth.core.exceptions import ImmediateHttpResponse
from django.test import RequestFactory
from fila.models import Barbeiro, ParticipacaoFila
from fila.services import entrar_na_fila, iniciar_atendimento, finalizar_corte, processar_filas_servico, alternar_status_cadeira, ConflitoEstadoError
from saas.models import Barbearia, VinculoBarbearia, Plano, ClienteCobranca, Assinatura, EventoStripe, TentativaContratacao
from saas.acesso import acesso_operacional, direito_financeiro, provisionar, aplicar_suspensao
from saas.adapters import GoogleAdapter
from saas import billing


class Fixture:
    def preparar(self):
        self.u = get_user_model().objects.create_user('owner', email='same@example.com')
        SocialAccount.objects.create(user=self.u, provider='google', uid='sub-owner')
        self.b = Barbearia.objects.create(nome='Barbearia A', slug='a', provisionamento='PRONTO')
        self.b2 = Barbearia.objects.create(nome='Barbearia B', slug='b', provisionamento='PRONTO')
        VinculoBarbearia.objects.create(usuario=self.u, barbearia=self.b)
        self.plano, _ = Plano.objects.update_or_create(codigo='mensal', defaults={'stripe_price_id_test': 'price_test', 'disponivel': True})
        self.user_b = get_user_model().objects.create_user('barber', password='Senha-valida-927!')
        self.c = Barbeiro.objects.create(barbearia=self.b, usuario=self.user_b, nome_exibicao='A', online=True)
        self.c2 = Barbeiro.objects.create(barbearia=self.b2, usuario=get_user_model().objects.create_user('barber2'), nome_exibicao='B', online=True)
        self.a = Assinatura.objects.create(barbearia=self.b, plano=self.plano, ambiente='test',
            stripe_subscription_id='sub_a', status_stripe='active', acesso_pago_desde=timezone.now()-timedelta(days=1),
            acesso_pago_ate=timezone.now()+timedelta(days=5))
        self.a2 = Assinatura.objects.create(barbearia=self.b2, plano=self.plano, ambiente='test',
            stripe_subscription_id='sub_b', status_stripe='active', acesso_pago_desde=timezone.now()-timedelta(days=1),
            acesso_pago_ate=timezone.now()+timedelta(days=5))

    def entrar(self, cadeira=None, numero=1):
        cadeira = cadeira or self.c
        return entrar_na_fila(cadeira.pk, 'Cliente', 'Teste', f'1198765432{numero}', date(1990, 1, 1), barbearia_id=cadeira.barbearia_id)


class TenancyTests(Fixture, TestCase):
    def setUp(self):
        self.preparar()

    def test_telefone_pode_repetir_entre_empresas(self):
        self.entrar(self.c)
        self.entrar(self.c2)
        self.assertEqual(ParticipacaoFila.objects.count(), 2)

    def test_cookie_nao_autoriza_outro_tenant(self):
        p, token, _ = self.entrar()
        self.client.cookies[settings.FILA_COOKIE_NOME] = token
        self.assertEqual(self.client.get('/b/b/api/cliente/acompanhamento/').json()['status'], 'ENCERRADO')
        self.assertTrue(ParticipacaoFila.objects.filter(pk=p.pk).exists())

    def test_barber_login_e_sessao_nao_operam_outro_tenant(self):
        self.assertEqual(self.client.post('/b/b/api/barbeiro/auth/login/', {'username': 'barber', 'senha': 'Senha-valida-927!'}, content_type='application/json').status_code, 401)
        self.client.force_login(self.user_b)
        self.assertEqual(self.client.get('/b/b/api/barbeiro/painel/').status_code, 403)

    def test_dono_nao_edita_ou_lista_empresa_alheia(self):
        self.client.force_login(self.u)
        self.assertEqual(self.client.get('/b/b/api/proprietario/filas/').status_code, 403)
        result = self.client.post(f'/b/a/api/proprietario/barbeiros/{self.c2.pk}/editar/', {'nome_exibicao': 'Invadido'}, content_type='application/json')
        self.assertEqual(result.status_code, 404)
        self.assertEqual(len(self.client.get('/b/a/api/proprietario/barbeiros/').json()['barbeiros']), 1)

    def test_admin_gts_nao_e_dono(self):
        admin = get_user_model().objects.create_superuser('admin', password='Senha-valida-972!')
        self.client.force_login(admin)
        self.assertEqual(self.client.get('/b/a/api/proprietario/filas/').status_code, 403)
        self.assertEqual(self.client.get('/plataforma/').status_code, 200)
        self.client.force_login(self.u)
        self.assertEqual(self.client.get('/plataforma/').status_code, 403)

    def test_id_de_cadeira_no_corpo_nao_autoriza(self):
        result = self.client.post(f'/b/a/api/publico/cadeiras/{self.c2.pk}/entrar/', {
            'primeiro_nome': 'Cliente', 'sobrenome': 'Teste', 'telefone': '11987654321', 'data_nascimento': '1990-01-01'}, content_type='application/json')
        self.assertEqual(result.status_code, 404)

    def test_banco_rejeita_relacao_incompativel(self):
        p, _, _ = self.entrar()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ParticipacaoFila.objects.filter(pk=p.pk).update(barbearia=self.b2)

    def test_suspensao_preserva_fila_e_finaliza_corte(self):
        p, _, _ = self.entrar()
        iniciar_atendimento(self.c, p.pk, str(p.identificador_chamada))
        espera, _, _ = self.entrar(numero=2)
        self.a.status_stripe = 'unpaid'; self.a.save()
        processar_filas_servico()
        self.c.refresh_from_db()
        self.assertFalse(self.c.online)
        self.assertTrue(ParticipacaoFila.objects.filter(pk=espera.pk).exists())
        with self.assertRaises(ConflitoEstadoError):
            alternar_status_cadeira(self.c, True)
        finalizar_corte(self.c, p.pk)
        espera.refresh_from_db()
        self.assertEqual(espera.status, 'AGUARDANDO')
        self.assertTrue(acesso_operacional(self.b2.pk).permitido)

    def test_primeira_cobranca_nao_recebe_tolerancia(self):
        self.a.acesso_pago_ate = None; self.a.status_stripe = 'past_due'; self.a.save()
        self.assertFalse(acesso_operacional(self.b.pk).permitido)

    def test_tolerancia_ancorada_no_periodo_pago(self):
        self.a.acesso_pago_ate = timezone.now() - timedelta(days=2)
        self.a.acesso_pago_desde = timezone.now() - timedelta(days=32)
        self.a.status_stripe = 'past_due'; self.a.save()
        self.assertEqual(acesso_operacional(self.b.pk).estado, 'TOLERANCIA')
        limite = self.a.acesso_pago_ate + timedelta(days=3)
        self.assertFalse(direito_financeiro(self.b, limite).permitido)
        self.assertEqual(direito_financeiro(self.b).limite, limite)

    def test_cancelamento_e_regularizacao_nao_ligam_cadeiras(self):
        self.a.cancelar_ao_final_do_periodo = True; self.a.save()
        self.assertEqual(acesso_operacional(self.b.pk).estado, 'CANCELAMENTO_AGENDADO')
        self.assertFalse(direito_financeiro(self.b, self.a.acesso_pago_ate).permitido)
        self.b.situacao_administrativa = 'BLOQUEADA'; self.b.save()
        aplicar_suspensao(self.b.pk)
        self.b.situacao_administrativa = 'ATIVA'; self.b.save()
        provisionar(self.b.pk)
        self.c.refresh_from_db()
        self.assertFalse(self.c.online)

    def test_conta_retomada_e_sucesso_nao_concedem_acesso(self):
        self.client.force_login(self.u)
        self.a.delete()
        self.assertEqual(self.client.get('/conta/retorno/').json()['estado'], 'SEM_ASSINATURA')
        self.assertFalse(acesso_operacional(self.b.pk).permitido)

    def test_onboarding_atomico_e_sem_equipe_ficticia(self):
        novo = get_user_model().objects.create_user('novo')
        SocialAccount.objects.create(user=novo, provider='google', uid='sub-novo')
        self.client.force_login(novo)
        self.assertEqual(self.client.get('/conta/').json()['estado'], 'CADASTRO_BARBEARIA')
        data = {'nome': 'Nova', 'slug': 'nova'}
        self.assertEqual(self.client.post('/conta/barbearia/', data, content_type='application/json').status_code, 201)
        self.assertEqual(self.client.post('/conta/barbearia/', data, content_type='application/json').status_code, 409)
        self.assertEqual(Barbearia.objects.get(slug='nova').barbeiros.count(), 0)

    def test_csrf_conta(self):
        client = Client(enforce_csrf_checks=True); client.force_login(self.u)
        self.assertEqual(client.post('/conta/checkout/').status_code, 403)

    def test_google_sub_identidade_e_email_sem_associacao(self):
        provider = GoogleProvider(RequestFactory().get('/'), app=SocialApp(provider='google', client_id='local'))
        self.assertEqual(provider.extract_uid({'sub': 'stable', 'email': 'a@b.com'}), 'stable')
        self.assertFalse(settings.SOCIALACCOUNT_EMAIL_AUTHENTICATION)
        self.assertFalse(settings.SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT)
        login = SocialLogin(user=self.user_b, account=SocialAccount(provider='google', uid='other'))
        request = RequestFactory().get('/'); request.user = self.user_b
        with self.assertRaises(ImmediateHttpResponse):
            GoogleAdapter().pre_social_login(request, login)

    def test_google_novo_e_existente_por_sub_mesmo_com_email_alterado(self):
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.middleware import SessionMiddleware
        from allauth.account.models import EmailAddress
        from allauth.core import context
        request = RequestFactory().get('/')
        SessionMiddleware(lambda r: None).process_request(request)
        request.user = AnonymousUser()
        provider = GoogleProvider(request, app=SocialApp(provider='google', client_id='local'))
        login = SocialLogin(user=get_user_model()(email=self.u.email),
            account=SocialAccount(provider='google', uid='new-google-sub'),
            email_addresses=[EmailAddress(email=self.u.email, verified=True, primary=True)])
        login.provider = provider
        with context.request_context(request):
            login.lookup()
            self.assertFalse(login.is_existing)
            GoogleAdapter().pre_social_login(request, login)
            new = GoogleAdapter().save_user(request, login)
            self.assertNotEqual(new.pk, self.u.pk)
            self.assertFalse(new.is_superuser)
            self.assertFalse(new.has_usable_password())
            again = SocialLogin(user=get_user_model()(email='changed@example.com'),
                account=SocialAccount(provider='google', uid='new-google-sub'))
            again.provider = provider
            again.lookup()
            self.assertEqual(again.user.pk, new.pk)

    def test_rotas_globais_operacionais_removidas(self):
        self.assertEqual(self.client.get('/api/publico/cadeiras/').status_code, 404)
        self.assertEqual(self.client.get('/b/inexistente/').status_code, 404)


class StripeTests(Fixture, TransactionTestCase):
    def setUp(self):
        self.preparar()
        self.a.delete()
        self.api = MagicMock()
        self.customer = ClienteCobranca.objects.create(barbearia=self.b, ambiente='test', stripe_customer_id='cus_a')
        self.remote_customer = {'id': 'cus_a', 'livemode': False, 'metadata': {'barbearia_id': str(self.b.pk)}}
        self.price = {'id': 'price_test', 'livemode': False, 'active': True, 'unit_amount': 10000, 'currency': 'brl',
            'billing_scheme': 'per_unit', 'recurring': {'interval': 'month', 'interval_count': 1, 'usage_type': 'licensed'}}
        self.api.customers.retrieve.return_value = self.remote_customer
        self.api.prices.retrieve.return_value = self.price
        self.api.subscriptions.list.return_value = {'data': [], 'has_more': False}
        self.api.checkout.sessions.list.return_value = {'data': [], 'has_more': False}
        self.api.invoices.list.return_value = {'data': [], 'has_more': False}
        self.patcher = patch('saas.billing.cliente_stripe', return_value=self.api); self.patcher.start(); self.addCleanup(self.patcher.stop)
        self.api.checkout.sessions.create.side_effect = self.session

    def session(self, params, options):
        self.assertFalse(connection.in_atomic_block, 'Chamada remota dentro de transação SQL')
        return dict(id='cs_a', status='open', livemode=False, url='https://checkout.stripe.com/test',
            mode='subscription', client_reference_id=str(self.b.pk), customer='cus_a', metadata=params['metadata'], expires_at=params['expires_at'])

    def subscription(self, tentativa=None, status='active'):
        now = int(time.time())
        return {'id': 'sub_remote', 'livemode': False, 'customer': 'cus_a', 'status': status, 'cancel_at_period_end': False,
            'metadata': {'barbearia_id': str(self.b.pk), 'tentativa_id': str(tentativa.pk) if tentativa else ''},
            'items': {'data': [{'quantity': 1, 'price': self.price, 'current_period_start': now - 20, 'current_period_end': now + 2678400}], 'has_more': False}}

    def paid(self, sub):
        now = int(time.time())
        invoice = {'id': 'in_paid', 'livemode': False, 'customer': 'cus_a', 'status': 'paid', 'amount_paid': 10000,
            'currency': 'brl', 'parent': {'subscription_details': {'subscription': sub['id']}}}
        self.api.invoices.list.return_value = {'data': [invoice], 'has_more': False}
        self.api.invoice_payments.list.return_value = {'data': [{'id': 'ip', 'amount_paid': 10000,
            'payment': {'type': 'payment_intent', 'payment_intent': 'pi_a'}}], 'has_more': False}
        self.api.payment_intents.retrieve.return_value = {'status': 'succeeded', 'livemode': False, 'customer': 'cus_a', 'currency': 'brl', 'amount_received': 10000}
        self.api.invoices.line_items.list.return_value = {'data': [{'id': 'il', 'quantity': 1, 'amount': 10000,
            'parent': {'subscription_item_details': {'subscription': sub['id'], 'proration': False}},
            'pricing': {'price_details': {'price': 'price_test'}}, 'period': {'start': now - 20, 'end': now + 2678400}}], 'has_more': False}

    def sync(self, sub):
        self.api.subscriptions.retrieve.return_value = sub
        with billing.reserva(self.b.pk) as token:
            return billing._sincronizar_assinatura(self.api, self.b, sub['id'], token)

    def test_duas_abas_reutilizam_checkout(self):
        t = billing.criar_checkout(self.b)
        self.api.checkout.sessions.retrieve.return_value = self.session(self.api.checkout.sessions.create.call_args.args[0], {})
        t2 = billing.criar_checkout(self.b)
        self.assertEqual(t.pk, t2.pk)
        self.assertEqual(self.api.checkout.sessions.create.call_count, 1)
        self.assertEqual(TentativaContratacao.objects.filter(aberta=True).count(), 1)

    def test_reserva_simultanea_rejeitada(self):
        with billing.reserva(self.b.pk):
            with self.assertRaisesMessage(billing.IntegracaoError, 'INTEGRACAO_EM_ANDAMENTO'):
                billing.criar_checkout(self.b)

    def test_duas_requisicoes_com_conexoes_independentes(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import close_old_connections
        iniciou, liberar = Event(), Event()
        def remoto(params, options):
            iniciou.set()
            if not liberar.wait(10): raise RuntimeError('timeout do teste')
            return self.session(params, options)
        self.api.checkout.sessions.create.side_effect = remoto
        def primeira():
            close_old_connections()
            try: return billing.criar_checkout(self.b)
            finally: connection.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(primeira)
            try:
                self.assertTrue(iniciou.wait(10))
                with self.assertRaisesMessage(billing.IntegracaoError, 'INTEGRACAO_EM_ANDAMENTO'):
                    billing.criar_checkout(self.b)
            finally:
                liberar.set()
            self.assertTrue(future.result(timeout=10).aberta)
        self.assertEqual(self.api.checkout.sessions.create.call_count, 1)

    def test_sdk16_converte_objetos_reais_na_fronteira(self):
        sdk = stripe.StripeClient('sk_test_local').v1
        with patch.object(sdk.subscriptions, 'retrieve', return_value=stripe.Subscription.construct_from(self.subscription(), 'sk_test_local')):
            normalized = billing.APIJson(sdk).subscriptions.retrieve('sub_remote')
        self.assertIsInstance(normalized, dict)
        self.assertEqual(normalized['items']['data'][0]['price'].get('unit_amount'), 10000)

    def test_timeout_reusa_chave_e_parametros(self):
        self.api.checkout.sessions.create.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            billing.criar_checkout(self.b)
        primeira = self.api.checkout.sessions.create.call_args
        self.api.checkout.sessions.create.side_effect = self.session
        billing.criar_checkout(self.b)
        self.assertEqual(primeira, self.api.checkout.sessions.create.call_args)
        self.assertEqual(TentativaContratacao.objects.count(), 1)

    def test_reserva_expirada_sem_sessao_e_reconciliada(self):
        self.api.checkout.sessions.create.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError): billing.criar_checkout(self.b)
        t = TentativaContratacao.objects.get(barbearia=self.b)
        t.expiracao = timezone.now() - timedelta(minutes=11); t.save()
        self.api.checkout.sessions.create.side_effect = self.session
        novo = billing.criar_checkout(self.b)
        t.refresh_from_db()
        self.assertFalse(t.aberta)
        self.assertNotEqual(t.pk, novo.pk)

    def test_conclusao_sem_pagamento_nao_libera(self):
        a = self.sync(self.subscription())
        self.assertIsNone(a.acesso_pago_ate)
        self.assertFalse(acesso_operacional(self.b.pk).permitido)

    def test_checkout_abandonado_expirado_permite_nova_tentativa(self):
        t = billing.criar_checkout(self.b)
        session = self.session(self.api.checkout.sessions.create.call_args.args[0], {})
        session['status'] = 'expired'; session['url'] = None
        self.api.checkout.sessions.retrieve.return_value = session
        def nova(params, options):
            result = self.session(params, options); result['id'] = 'cs_nova'; return result
        self.api.checkout.sessions.create.side_effect = nova
        novo = billing.criar_checkout(self.b)
        t.refresh_from_db()
        self.assertFalse(t.aberta)
        self.assertNotEqual(t.pk, novo.pk)

    def test_sem_barbeiros_provisiona_para_etapa_equipe(self):
        self.c.delete()
        sub = self.subscription(); self.paid(sub); self.sync(sub)
        self.b.refresh_from_db()
        self.assertEqual(self.b.provisionamento, 'EQUIPE')
        self.assertTrue(direito_financeiro(self.b).permitido)
        self.assertFalse(acesso_operacional(self.b.pk).permitido)

    def test_pagamento_confirmado_provisiona_e_renovacao_nao_duplica(self):
        sub = self.subscription(); self.paid(sub)
        a = self.sync(sub)
        self.assertEqual(int(a.acesso_pago_ate.timestamp()), self.api.invoices.line_items.list.return_value['data'][0]['period']['end'])
        self.assertTrue(acesso_operacional(self.b.pk).permitido)
        self.sync(sub)
        self.assertEqual(Assinatura.objects.filter(barbearia=self.b).count(), 1)
        self.assertEqual(Barbearia.objects.count(), 2)

    def test_pagamento_fora_da_stripe_nao_libera(self):
        sub = self.subscription(); self.paid(sub)
        self.api.invoice_payments.list.return_value = {'data': [], 'has_more': False}
        self.assertIsNone(self.sync(sub).acesso_pago_ate)

    def test_renovacao_antecipada_preserva_periodo_pago_vigente(self):
        sub = self.subscription(); self.paid(sub)
        anterior = self.sync(sub)
        line = self.api.invoices.line_items.list.return_value['data'][0]
        line['period']['start'] = int(anterior.acesso_pago_ate.timestamp())
        line['period']['end'] = int((anterior.acesso_pago_ate + timedelta(days=31)).timestamp())
        renovada = self.sync(sub)
        self.assertEqual(renovada.acesso_pago_desde, anterior.acesso_pago_desde)
        self.assertTrue(acesso_operacional(self.b.pk).permitido)

    def test_bloqueio_administrativo_nao_reativa(self):
        self.b.situacao_administrativa = 'BLOQUEADA'; self.b.save()
        sub = self.subscription(); self.paid(sub); self.sync(sub)
        self.assertFalse(acesso_operacional(self.b.pk).permitido)

    def test_falha_provisionamento_retoma_sem_cobrar(self):
        sub = self.subscription(); self.paid(sub)
        with patch('saas.billing.provisionar', side_effect=RuntimeError('local')):
            with self.assertRaises(RuntimeError): self.sync(sub)
        self.assertIsNotNone(Assinatura.objects.get(barbearia=self.b).acesso_pago_ate)
        self.sync(sub)
        self.api.checkout.sessions.create.assert_not_called()

    def test_price_ou_customer_errado_rejeitados(self):
        sub = self.subscription(); sub['customer'] = 'cus_other'
        with self.assertRaises(billing.IntegracaoError): self.sync(sub)
        sub = self.subscription(); sub['items']['data'][0]['price'] = dict(self.price, unit_amount=1)
        with self.assertRaises(billing.IntegracaoError): self.sync(sub)

    def test_recontratacao_preserva_historico(self):
        sub = self.subscription(); self.paid(sub); self.sync(sub)
        sub['status'] = 'canceled'; self.sync(sub)
        novo = self.subscription(); novo['id'] = 'sub_novo'; self.paid(novo); self.sync(novo)
        self.assertEqual(Assinatura.objects.filter(barbearia=self.b).count(), 2)
        self.assertEqual(Assinatura.objects.filter(barbearia=self.b, encerrada=False).count(), 1)

    @override_settings(STRIPE_WEBHOOK_SECRET='whsec_local')
    def test_webhook_invalido_duplicado_e_duravel(self):
        event = {'id': 'evt_test', 'type': 'invoice.paid', 'livemode': False, 'data': {'object': {'id': 'in_test'}}}
        raw = json.dumps(event).encode(); now = int(time.time())
        signature = hmac.new(b'whsec_local', str(now).encode()+b'.'+raw, hashlib.sha256).hexdigest()
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/stripe/webhook/', raw, content_type='application/json', HTTP_STRIPE_SIGNATURE='bad').status_code, 400)
        for _ in range(2):
            self.assertEqual(client.post('/stripe/webhook/', raw, content_type='application/json', HTTP_STRIPE_SIGNATURE=f't={now},v1={signature}').status_code, 200)
        self.assertEqual(EventoStripe.objects.count(), 1)
        self.assertEqual(EventoStripe.objects.get().situacao, 'PENDENTE')
        self.assertFalse(Assinatura.objects.filter(barbearia=self.b).exists())

    def test_evento_atrasado_consulta_estado_atual(self):
        sub = self.subscription(status='canceled'); self.paid(sub)
        self.api.subscriptions.retrieve.return_value = sub
        self.api.subscriptions.list.return_value = {'data': [sub], 'has_more': False}
        e = EventoStripe.objects.create(ambiente='test', stripe_event_id='evt_old', tipo='customer.subscription.created', objeto_id=sub['id'])
        billing.processar_evento(e.pk)
        self.assertEqual(Assinatura.objects.get(barbearia=self.b).status_stripe, 'canceled')
        e.refresh_from_db(); self.assertEqual(e.situacao, 'PROCESSADO')

    def test_evento_erro_persistido_e_limite(self):
        e = EventoStripe.objects.create(ambiente='test', stripe_event_id='evt_err', tipo='invoice.paid', objeto_id='in')
        self.api.invoices.retrieve.side_effect = RuntimeError('segredo-nao-deve-vazar')
        billing.processar_evento(e.pk)
        e.refresh_from_db(); self.assertEqual(e.erro_tecnico, 'RuntimeError')
        self.assertEqual(e.tentativas, 1)
        billing.processar_evento(e.pk)
        e.refresh_from_db(); self.assertEqual(e.tentativas, 1)

    def test_portal_usa_customer_do_dono_e_valida_configuracao(self):
        with override_settings(STRIPE_PORTAL_CONFIGURATION_ID='bpc'):
            self.api.billing_portal.configurations.retrieve.return_value = {'id': 'bpc', 'active': True,
                'features': {'subscription_update': {'enabled': False}, 'subscription_cancel': {'enabled': True, 'mode': 'at_period_end'}}}
            self.api.billing_portal.sessions.create.return_value = {'url': 'https://billing.stripe.com/test'}
            billing.criar_portal(self.b)
            self.assertEqual(self.api.billing_portal.sessions.create.call_args.args[0]['customer'], 'cus_a')
