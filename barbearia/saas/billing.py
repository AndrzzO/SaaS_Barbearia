"""Stripe 16 / API 2026-09-30.endive. Rede sempre fora de transações SQL.

Uma lease persistida por empresa serializa Checkout, eventos e reconciliação.
As reservas e chaves idempotentes sobrevivem a timeout e reinício do processo.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as dt_timezone
import uuid
import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import Barbearia, Plano, ClienteCobranca, Assinatura, TentativaContratacao, EventoStripe
from .acesso import provisionar, aplicar_suspensao, direito_financeiro

API_VERSION = '2026-09-30.endive'
TERMINAIS = {'canceled', 'incomplete_expired'}


class IntegracaoError(Exception):
    pass


class APIJson:
    """Normaliza a fronteira do SDK 16: StripeObject deixou de ser dict."""
    def __init__(self, service, budget=None):
        self.service = service
        self.budget = budget if budget is not None else [100]

    def __getattr__(self, name):
        value = getattr(self.service, name)
        if not callable(value):
            return APIJson(value, self.budget)

        def invoke(*args, **kwargs):
            if self.budget[0] <= 0:
                raise IntegracaoError('LIMITE_CHAMADAS_REQUER_REVISAO')
            self.budget[0] -= 1
            result = value(*args, **kwargs)
            return result.to_dict() if isinstance(result, stripe.StripeObject) else result
        return invoke


def cliente_stripe():
    key = settings.STRIPE_SECRET_KEY
    prefix = 'sk_live_' if settings.STRIPE_AMBIENTE == 'live' else 'sk_test_'
    if not key.startswith(prefix):
        raise IntegracaoError('STRIPE_NAO_CONFIGURADA')
    return APIJson(stripe.StripeClient(key, stripe_version=API_VERSION, max_network_retries=1,
                               http_client=stripe.RequestsClient(timeout=15)).v1)


def dt(value):
    return datetime.fromtimestamp(value, dt_timezone.utc) if value else None


def ident(value):
    return value.get('id') if isinstance(value, dict) or hasattr(value, 'get') else value


def validar_ambiente(obj):
    if obj.get('livemode') is not (settings.STRIPE_AMBIENTE == 'live'):
        raise IntegracaoError('AMBIENTE_DIVERGENTE')


def erro_seguro(exc):
    return str(exc) if isinstance(exc, IntegracaoError) else type(exc).__name__[:100]


@contextmanager
def reserva(barbearia_id):
    token = uuid.uuid4()
    with transaction.atomic():
        b = Barbearia.objects.get(pk=barbearia_id)
        if b.integracao_ate and b.integracao_ate > timezone.now():
            raise IntegracaoError('INTEGRACAO_EM_ANDAMENTO')
        b.integracao_token = token
        b.integracao_ate = timezone.now() + timedelta(minutes=10)
        b.save(update_fields=['integracao_token', 'integracao_ate'])
    try:
        yield token
    finally:
        Barbearia.objects.filter(pk=barbearia_id, integracao_token=token).update(integracao_token=None, integracao_ate=None)


def verificar_reserva(barbearia_id, token):
    if not Barbearia.objects.filter(pk=barbearia_id, integracao_token=token, integracao_ate__gt=timezone.now()).exists():
        raise IntegracaoError('RESERVA_EXPIRADA')


def paginas(service, params, limite=10):
    """Limite explícito: exceder o orçamento gera erro, nunca conclusão parcial."""
    cursor = None
    for _ in range(limite):
        args = dict(params, limit=100)
        if cursor:
            args['starting_after'] = cursor
        page = service.list(args)
        yield from page['data']
        if not page['has_more']:
            return
        cursor = page['data'][-1]['id']
    raise IntegracaoError('LIMITE_PAGINACAO_REQUER_REVISAO')


def validar_preco(price, price_id):
    validar_ambiente(price)
    recurrence = price.get('recurring') or {}
    if (price['id'] != price_id or price.get('unit_amount') != 10000 or price.get('currency') != 'brl'
            or recurrence.get('interval') != 'month' or recurrence.get('interval_count') != 1
            or recurrence.get('usage_type') != 'licensed' or price.get('billing_scheme') != 'per_unit'):
        raise IntegracaoError('PRECO_NAO_AUTORIZADO')


def obter_customer(api, b, token):
    with transaction.atomic():
        verificar_reserva(b.pk, token)
        local, _ = ClienteCobranca.objects.get_or_create(barbearia=b, ambiente=settings.STRIPE_AMBIENTE)
    if not local.stripe_customer_id:
        # Após a janela de idempotência, não arriscamos criar um segundo Customer.
        if timezone.now() - local.criado_em >= timedelta(hours=23):
            raise IntegracaoError('CUSTOMER_INDETERMINADO_REQUER_RECUPERACAO')
        obj = api.customers.create({'metadata': {'barbearia_id': str(b.pk)}},
                                   options={'idempotency_key': str(local.chave_idempotencia)})
        validar_ambiente(obj)
        with transaction.atomic():
            verificar_reserva(b.pk, token)
            local.stripe_customer_id = obj['id']
            local.save(update_fields=['stripe_customer_id', 'atualizado_em'])
    remote = api.customers.retrieve(local.stripe_customer_id)
    validar_ambiente(remote)
    if remote.get('deleted') or remote.get('metadata', {}).get('barbearia_id') != str(b.pk):
        raise IntegracaoError('CUSTOMER_DIVERGENTE')
    return local


def _persistir_sessao(b, tentativa, sessao, token):
    validar_ambiente(sessao)
    if sessao.get('mode') != 'subscription' or sessao.get('client_reference_id') != str(b.pk):
        raise IntegracaoError('CHECKOUT_DIVERGENTE')
    customer = ClienteCobranca.objects.get(barbearia=b, ambiente=settings.STRIPE_AMBIENTE)
    if ident(sessao.get('customer')) != customer.stripe_customer_id or sessao.get('metadata', {}).get('tentativa_id') != str(tentativa.pk):
        raise IntegracaoError('CHECKOUT_VINCULO_DIVERGENTE')
    state = sessao['status']
    with transaction.atomic():
        verificar_reserva(b.pk, token)
        tentativa.stripe_checkout_session_id = sessao['id']
        tentativa.situacao = {'open': 'ABERTA', 'complete': 'CONCLUIDA', 'expired': 'EXPIRADA'}[state]
        # Complete permanece reservado até a assinatura ser sincronizada.
        tentativa.aberta = state != 'expired'
        tentativa.url = sessao.get('url') or ''
        tentativa.expiracao = dt(sessao['expires_at'])
        tentativa.save()
    return ident(sessao.get('subscription'))


def _encerrar_reserva_sem_sessao(b, tentativa, token):
    """Só após listagem remota completa sem match e expiração fixa + margem de lease.

    Uma requisição antiga tem expires_at já vencido e não pode criar Checkout válido.
    Preserva a tentativa para diagnóstico, em vez de apagar seu histórico.
    """
    if timezone.now() < tentativa.expiracao + timedelta(minutes=10):
        raise IntegracaoError('CHECKOUT_INDETERMINADO_AGUARDE_RECONCILIACAO')
    with transaction.atomic():
        verificar_reserva(b.pk, token)
        if Assinatura.objects.filter(barbearia=b, ambiente=settings.STRIPE_AMBIENTE, encerrada=False).exists():
            raise IntegracaoError('ASSINATURA_EXISTENTE_USE_PORTAL')
        tentativa.situacao = 'SEM_SESSAO_EXPIRADA'
        tentativa.aberta = False
        tentativa.save(update_fields=['situacao', 'aberta', 'atualizado_em'])


def _checkout(api, b, token):
    if b.situacao_administrativa != 'ATIVA':
        raise IntegracaoError('BARBEARIA_BLOQUEADA')
    if not b.vinculos.filter(ativo=True, papel='DONO').exists():
        raise IntegracaoError('DONO_AUSENTE')
    customer = obter_customer(api, b, token)
    # Estado remoto vence reservas locais após falha local/timeout.
    remotas = list(paginas(api.subscriptions, {'customer': customer.stripe_customer_id, 'status': 'all'}))
    nao_encerradas = [s for s in remotas if s['status'] not in TERMINAIS]
    if nao_encerradas:
        if len(nao_encerradas) != 1:
            raise IntegracaoError('MULTIPLAS_ASSINATURAS_REQUER_REVISAO')
        _sincronizar_assinatura(api, b, nao_encerradas[0]['id'], token)
        raise IntegracaoError('ASSINATURA_EXISTENTE_USE_PORTAL')
    for a in Assinatura.objects.filter(barbearia=b, ambiente=settings.STRIPE_AMBIENTE, encerrada=False):
        _sincronizar_assinatura(api, b, a.stripe_subscription_id, token)
    tentativa = TentativaContratacao.objects.filter(barbearia=b, ambiente=settings.STRIPE_AMBIENTE, aberta=True).first()
    if tentativa:
        if tentativa.stripe_checkout_session_id:
            sessao = api.checkout.sessions.retrieve(tentativa.stripe_checkout_session_id)
        else:
            matches = [s for s in paginas(api.checkout.sessions, {'customer': customer.stripe_customer_id})
                       if s.get('metadata', {}).get('tentativa_id') == str(tentativa.pk)]
            sessao = matches[0] if len(matches) == 1 else None
            if len(matches) > 1:
                raise IntegracaoError('MULTIPLOS_CHECKOUTS_REQUER_REVISAO')
        if sessao:
            sub = _persistir_sessao(b, tentativa, sessao, token)
            if sub:
                _sincronizar_assinatura(api, b, sub, token)
                raise IntegracaoError('ASSINATURA_EXISTENTE_USE_PORTAL')
            if tentativa.aberta:
                return tentativa
        elif timezone.now() >= tentativa.expiracao:
            _encerrar_reserva_sem_sessao(b, tentativa, token)
    if not tentativa or not tentativa.aberta:
        plano = Plano.objects.get(codigo='mensal', disponivel=True)
        price = api.prices.retrieve(plano.price_id())
        validar_preco(price, plano.price_id())
        if not price['active']:
            raise IntegracaoError('PRECO_INATIVO')
        with transaction.atomic():
            verificar_reserva(b.pk, token)
            tentativa = TentativaContratacao.objects.create(barbearia=b, plano=plano,
                ambiente=settings.STRIPE_AMBIENTE, stripe_price_id=plano.price_id(),
                expiracao=(timezone.now() + timedelta(hours=1)).replace(microsecond=0))
    base = settings.SAAS_BASE_URL.rstrip('/')
    sessao = api.checkout.sessions.create({
        'mode': 'subscription', 'customer': customer.stripe_customer_id,
        'payment_method_types': ['card'], 'allow_promotion_codes': False,
        'line_items': [{'price': tentativa.stripe_price_id, 'quantity': 1}],
        'client_reference_id': str(b.pk),
        'metadata': {'barbearia_id': str(b.pk), 'tentativa_id': str(tentativa.pk)},
        'subscription_data': {'metadata': {'barbearia_id': str(b.pk), 'tentativa_id': str(tentativa.pk)}},
        'expires_at': int(tentativa.expiracao.timestamp()),
        'success_url': base + '/conta/retorno/', 'cancel_url': base + '/conta/',
    }, options={'idempotency_key': str(tentativa.chave_idempotencia)})
    _persistir_sessao(b, tentativa, sessao, token)
    return tentativa


def criar_checkout(barbearia):
    try:
        with reserva(barbearia.pk) as token:
            result = _checkout(cliente_stripe(), Barbearia.objects.get(pk=barbearia.pk), token)
            Barbearia.objects.filter(pk=barbearia.pk).update(erro_integracao='')
            return result
    except (stripe.StripeError, TimeoutError) as exc:
        Barbearia.objects.filter(pk=barbearia.pk).update(erro_integracao=erro_seguro(exc))
        raise


def _periodo_pago(api, invoice, sub, customer, price_id):
    validar_ambiente(invoice)
    parent = (invoice.get('parent') or {}).get('subscription_details') or {}
    if ident(parent.get('subscription')) != sub['id'] or ident(invoice.get('customer')) != customer:
        raise IntegracaoError('FATURA_VINCULO_DIVERGENTE')
    if invoice.get('status') != 'paid' or invoice.get('amount_paid', 0) < 10000 or invoice.get('currency') != 'brl':
        return None
    # Exige evidência de pagamento Stripe; marcar fatura paga fora da Stripe não concede acesso.
    pagamentos = list(paginas(api.invoice_payments, {'invoice': invoice['id'], 'status': 'paid'}))
    total = 0
    for p in pagamentos:
        payment = p.get('payment') or {}
        if payment.get('type') == 'payment_intent':
            pi = api.payment_intents.retrieve(ident(payment['payment_intent']))
            validar_ambiente(pi)
            if pi['status'] == 'succeeded' and pi['currency'] == 'brl' and ident(pi.get('customer')) == customer:
                total += min(p.get('amount_paid', 0), pi.get('amount_received', 0))
    if total < 10000:
        return None
    # A coleção de linhas é paginada separadamente da fatura.
    page = api.invoices.line_items.list(invoice['id'], {'limit': 100})
    if page['has_more']:
        raise IntegracaoError('FATURA_COMPLEXA_REQUER_REVISAO')
    periodos = []
    for line in page['data']:
        detail = (line.get('parent') or {}).get('subscription_item_details') or {}
        price = ((line.get('pricing') or {}).get('price_details') or {}).get('price')
        if (ident(detail.get('subscription')) == sub['id'] and not detail.get('proration')
                and ident(price) == price_id and line.get('quantity') == 1 and line.get('amount') == 10000):
            periodos.append((dt(line['period']['start']), dt(line['period']['end']), invoice['id']))
    return max(periodos, key=lambda p: p[1]) if periodos else None


def _sincronizar_assinatura(api, b, subscription_id, token):
    aplicar_suspensao(b.pk)
    sub = api.subscriptions.retrieve(subscription_id)
    validar_ambiente(sub)
    customer = ClienteCobranca.objects.get(barbearia=b, ambiente=settings.STRIPE_AMBIENTE)
    if ident(sub.get('customer')) != customer.stripe_customer_id or sub.get('metadata', {}).get('barbearia_id') != str(b.pk):
        raise IntegracaoError('ASSINATURA_VINCULO_DIVERGENTE')
    plano = Plano.objects.get(codigo='mensal')
    items = sub['items']['data']
    if sub['items'].get('has_more') or len(items) != 1 or items[0].get('quantity') != 1 or sub.get('discounts') or sub.get('trial_end'):
        raise IntegracaoError('ASSINATURA_FORA_DO_PLANO')
    validar_preco(items[0]['price'], plano.price_id())
    periodo = None
    # Recupera a última cobrança realmente paga também quando latest_invoice está em atraso.
    for invoice in paginas(api.invoices, {'subscription': sub['id'], 'status': 'paid'}, limite=3):
        result = _periodo_pago(api, invoice, sub, customer.stripe_customer_id, plano.price_id())
        if result:
            periodo = result
            break
    with transaction.atomic():
        verificar_reserva(b.pk, token)
        a, _ = Assinatura.objects.get_or_create(barbearia=b, ambiente=settings.STRIPE_AMBIENTE,
            stripe_subscription_id=sub['id'], defaults={'plano': plano, 'status_stripe': sub['status'], 'encerrada': sub['status'] in TERMINAIS})
        # Uma assinatura encerrada nunca ressuscita por uma resposta obsoleta.
        if a.encerrada and sub['status'] not in TERMINAIS:
            raise IntegracaoError('ASSINATURA_ENCERRADA_DIVERGENTE')
        a.status_stripe = sub['status']
        a.encerrada = sub['status'] in TERMINAIS
        a.cancelar_ao_final_do_periodo = sub['cancel_at_period_end']
        a.periodo_inicio = dt(items[0]['current_period_start'])
        a.periodo_fim = dt(items[0]['current_period_end'])
        if periodo and (not a.acesso_pago_ate or periodo[1] > a.acesso_pago_ate):
            inicio, fim, fatura = periodo
            if not a.acesso_pago_ate or not a.acesso_pago_desde or inicio > a.acesso_pago_ate:
                a.acesso_pago_desde = inicio
            a.acesso_pago_ate, a.ultima_fatura_paga = fim, fatura
        a.sincronizado_em = timezone.now()
        a.save()
        a.situacao_local = direito_financeiro(b).estado
        a.save(update_fields=['situacao_local'])
        TentativaContratacao.objects.filter(barbearia=b, ambiente=settings.STRIPE_AMBIENTE,
            pk=sub.get('metadata', {}).get('tentativa_id') or uuid.UUID(int=0)).update(aberta=False, situacao='CONCLUIDA')
        Barbearia.objects.filter(pk=b.pk).update(sincronizado_em=timezone.now(), erro_integracao='')
        aplicar_suspensao(b.pk)
    # Falha daqui em diante é recuperada sem nova contratação.
    try:
        provisionar(b.pk)
    except Exception as exc:
        Barbearia.objects.filter(pk=b.pk).update(provisionamento='ERRO', erro_provisionamento=erro_seguro(exc))
        raise
    return a


def sincronizar_empresa(barbearia_id):
    with reserva(barbearia_id) as token:
        api = cliente_stripe()
        b = Barbearia.objects.get(pk=barbearia_id)
        customer = ClienteCobranca.objects.filter(barbearia=b, ambiente=settings.STRIPE_AMBIENTE).first()
        if not customer:
            aplicar_suspensao(b.pk)
            Barbearia.objects.filter(pk=b.pk).update(sincronizado_em=timezone.now(), erro_integracao='')
            return
        customer = obter_customer(api, b, token)
        subs = list(paginas(api.subscriptions, {'customer': customer.stripe_customer_id, 'status': 'all'}))
        if sum(s['status'] not in TERMINAIS for s in subs) > 1:
            raise IntegracaoError('MULTIPLAS_ASSINATURAS_REQUER_REVISAO')
        # Persiste encerramentos antes de importar uma recontratação.
        for sub in sorted(subs, key=lambda s: s['status'] not in TERMINAIS):
            if sub['status'] in TERMINAIS and Assinatura.objects.filter(
                    barbearia=b, ambiente=settings.STRIPE_AMBIENTE, stripe_subscription_id=sub['id'], encerrada=True).exists():
                continue
            _sincronizar_assinatura(api, b, sub['id'], token)
        for t in TentativaContratacao.objects.filter(barbearia=b, ambiente=settings.STRIPE_AMBIENTE, aberta=True):
            sessions = [api.checkout.sessions.retrieve(t.stripe_checkout_session_id)] if t.stripe_checkout_session_id else list(paginas(api.checkout.sessions, {'customer': customer.stripe_customer_id}))
            matches = [s for s in sessions if s.get('metadata', {}).get('tentativa_id') == str(t.pk)]
            if len(matches) > 1:
                raise IntegracaoError('MULTIPLOS_CHECKOUTS_REQUER_REVISAO')
            if matches:
                sub_id = _persistir_sessao(b, t, matches[0], token)
                if sub_id:
                    _sincronizar_assinatura(api, b, sub_id, token)
            elif timezone.now() >= t.expiracao:
                _encerrar_reserva_sem_sessao(b, t, token)
        provisionar(b.pk)
        aplicar_suspensao(b.pk)
        with transaction.atomic():
            verificar_reserva(b.pk, token)
            Barbearia.objects.filter(pk=b.pk).update(sincronizado_em=timezone.now(), erro_integracao='')


def criar_portal(barbearia):
    customer = ClienteCobranca.objects.get(barbearia=barbearia, ambiente=settings.STRIPE_AMBIENTE)
    if not settings.STRIPE_PORTAL_CONFIGURATION_ID or not customer.stripe_customer_id:
        raise IntegracaoError('PORTAL_NAO_CONFIGURADO')
    api = cliente_stripe()
    conf = api.billing_portal.configurations.retrieve(settings.STRIPE_PORTAL_CONFIGURATION_ID)
    features = conf['features']
    cancel = features['subscription_cancel']
    if (not conf['active'] or features['subscription_update']['enabled'] or not cancel['enabled']
            or cancel['mode'] != 'at_period_end'):
        raise IntegracaoError('PORTAL_CONFIGURACAO_INVALIDA')
    return api.billing_portal.sessions.create({'customer': customer.stripe_customer_id,
        'configuration': conf['id'], 'return_url': settings.SAAS_BASE_URL.rstrip('/') + '/conta/'})['url']


def alterar_cancelamento(barbearia, cancelar):
    with reserva(barbearia.pk) as token:
        api = cliente_stripe()
        a = Assinatura.objects.get(barbearia=barbearia, ambiente=settings.STRIPE_AMBIENTE, encerrada=False)
        sub = api.subscriptions.retrieve(a.stripe_subscription_id)
        if sub['status'] not in ('active', 'past_due'):
            raise IntegracaoError('CANCELAMENTO_INELEGIVEL')
        api.subscriptions.update(a.stripe_subscription_id, {'cancel_at_period_end': cancelar})
        return _sincronizar_assinatura(api, barbearia, a.stripe_subscription_id, token)


EVENTOS = {'checkout.session.completed', 'checkout.session.expired',
    'customer.subscription.created', 'customer.subscription.updated', 'customer.subscription.deleted',
    'invoice.paid', 'invoice.payment_failed', 'invoice.payment_action_required', 'invoice.finalization_failed',
    'charge.refunded', 'charge.dispute.created', 'charge.dispute.updated', 'charge.dispute.closed'}


def receber_evento(raw, signature):
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise IntegracaoError('WEBHOOK_NAO_CONFIGURADO')
    event = stripe.Webhook.construct_event(raw, signature, settings.STRIPE_WEBHOOK_SECRET).to_dict()
    validar_ambiente(event)
    if event['type'] not in EVENTOS:
        return None
    # O worker recupera os objetos atuais pelo ID. Nenhum payload/PII é persistido.
    obj, _ = EventoStripe.objects.get_or_create(ambiente=settings.STRIPE_AMBIENTE, stripe_event_id=event['id'],
        defaults={'tipo': event['type'], 'objeto_id': event['data']['object']['id']})
    return obj


def processar_evento(evento_id):
    with transaction.atomic():
        e = EventoStripe.objects.get(pk=evento_id, ambiente=settings.STRIPE_AMBIENTE)
        if e.situacao in ('PROCESSADO', 'ANALISE') or e.tentativas >= settings.STRIPE_MAX_TENTATIVAS:
            return
        if e.proxima_tentativa_em and e.proxima_tentativa_em > timezone.now():
            return
        e.situacao = 'PROCESSANDO'
        e.tentativas += 1
        e.proxima_tentativa_em = timezone.now() + timedelta(minutes=10)
        e.save(update_fields=['situacao', 'tentativas', 'proxima_tentativa_em'])
        lease = e.proxima_tentativa_em
    try:
        if e.tipo.startswith('charge.'):
            e.situacao = 'ANALISE'
        else:
            api = cliente_stripe()
            if e.tipo.startswith('checkout.'):
                obj = api.checkout.sessions.retrieve(e.objeto_id)
            elif e.tipo.startswith('customer.subscription.'):
                obj = api.subscriptions.retrieve(e.objeto_id)
            else:
                obj = api.invoices.retrieve(e.objeto_id)
            validar_ambiente(obj)
            customer = ClienteCobranca.objects.filter(ambiente=e.ambiente, stripe_customer_id=ident(obj.get('customer'))).first()
            if not customer:
                raise IntegracaoError('CUSTOMER_LOCAL_AUSENTE')
            sincronizar_empresa(customer.barbearia_id)
            e.situacao = 'PROCESSADO'
        e.processado_em = timezone.now()
        e.erro_tecnico = ''
    except Exception as exc:
        e.situacao = 'ERRO'
        e.erro_tecnico = erro_seguro(exc)
        e.proxima_tentativa_em = timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** e.tentativas))
    EventoStripe.objects.filter(pk=e.pk, situacao='PROCESSANDO', proxima_tentativa_em=lease).update(
        situacao=e.situacao, processado_em=e.processado_em, erro_tecnico=e.erro_tecnico,
        proxima_tentativa_em=e.proxima_tentativa_em)
