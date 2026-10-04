"""Recuperação explícita de IDs remotos após esgotar a janela de idempotência."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from saas.models import Barbearia, ClienteCobranca, TentativaContratacao
from saas import billing


class Command(BaseCommand):
    help = 'Reassocia Customer ou Checkout remoto validando metadata; nunca marca assinatura paga.'

    def add_arguments(self, parser):
        parser.add_argument('--slug', required=True)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--customer-id')
        group.add_argument('--checkout-id')

    def handle(self, *args, **opts):
        b = Barbearia.objects.get(slug=opts['slug'])
        api = billing.cliente_stripe()
        with billing.reserva(b.pk) as token:
            if opts['customer_id']:
                remote = api.customers.retrieve(opts['customer_id'])
                billing.validar_ambiente(remote)
                if remote.get('deleted') or remote.get('metadata', {}).get('barbearia_id') != str(b.pk):
                    raise CommandError('Customer não pertence a esta barbearia.')
                with transaction.atomic():
                    billing.verificar_reserva(b.pk, token)
                    local = ClienteCobranca.objects.get(barbearia=b, ambiente=settings.STRIPE_AMBIENTE)
                    if local.stripe_customer_id and local.stripe_customer_id != remote['id']:
                        raise CommandError('Customer local já definido; troca não permitida.')
                    local.stripe_customer_id = remote['id']
                    local.save(update_fields=['stripe_customer_id'])
            else:
                session = api.checkout.sessions.retrieve(opts['checkout_id'])
                tentativa = TentativaContratacao.objects.get(barbearia=b, ambiente=settings.STRIPE_AMBIENTE,
                    pk=session.get('metadata', {}).get('tentativa_id'))
                billing._persistir_sessao(b, tentativa, session, token)
        self.stdout.write('Vínculo remoto recuperado. Execute reconciliar_saas para verificar a cobrança.')
