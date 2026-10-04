import stripe
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from saas.models import Plano
from saas.billing import cliente_stripe, validar_preco


class Command(BaseCommand):
    help = 'Cria produto, Price mensal e Portal em TESTE; em produção apenas valida Price informado.'

    def add_arguments(self, parser):
        parser.add_argument('--price-id')

    def handle(self, *args, **opts):
        api = cliente_stripe()
        price_id = opts['price_id']
        if not price_id:
            if settings.STRIPE_AMBIENTE != 'test':
                raise CommandError('Criação automática somente em teste. Informe --price-id validado em produção.')
            try:
                product = api.products.retrieve('gts_barbearia_mensal')
            except stripe.InvalidRequestError as exc:
                if exc.http_status != 404:
                    raise
                product = api.products.create({'id': 'gts_barbearia_mensal', 'name': 'GTS Barbearia Mensal'},
                    options={'idempotency_key': 'gts-produto-mensal-v1'})
            existing = api.prices.list({'lookup_keys': ['gts_mensal_brl_100'], 'limit': 1})['data']
            price = existing[0] if existing else api.prices.create({'product': product['id'], 'currency': 'brl',
                'unit_amount': 10000, 'recurring': {'interval': 'month'}, 'lookup_key': 'gts_mensal_brl_100'},
                options={'idempotency_key': 'gts-preco-mensal-v1'})
            price_id = price['id']
        price = api.prices.retrieve(price_id)
        validar_preco(price, price_id)
        plano, _ = Plano.objects.get_or_create(codigo='mensal')
        setattr(plano, 'stripe_price_id_' + settings.STRIPE_AMBIENTE, price_id)
        plano.disponivel = True
        plano.save()
        self.stdout.write(f'Price validado: {price_id}')
        if settings.STRIPE_AMBIENTE == 'test':
            config = api.billing_portal.configurations.create({'business_profile': {'headline': 'Guild Tech Support'},
                'features': {'customer_update': {'enabled': False}, 'invoice_history': {'enabled': True},
                    'payment_method_update': {'enabled': True}, 'subscription_update': {'enabled': False},
                    'subscription_cancel': {'enabled': True, 'mode': 'at_period_end'}}},
                options={'idempotency_key': 'gts-portal-v1'})
            self.stdout.write('STRIPE_PORTAL_CONFIGURATION_ID=' + config['id'])
