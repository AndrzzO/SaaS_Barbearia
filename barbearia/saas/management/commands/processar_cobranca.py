import time
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from saas.models import EventoStripe
from saas.billing import processar_evento


class Command(BaseCommand):
    help = 'Worker durável de eventos Stripe, separado do worker da fila.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--intervalo', type=float, default=5)
        parser.add_argument('--limite', type=int, default=50)

    def handle(self, *args, **opts):
        try:
            while True:
                ids = list(EventoStripe.objects.filter(ambiente=settings.STRIPE_AMBIENTE,
                    situacao__in=['PENDENTE', 'ERRO', 'PROCESSANDO'], tentativas__lt=settings.STRIPE_MAX_TENTATIVAS
                    ).filter(Q(proxima_tentativa_em__isnull=True) | Q(proxima_tentativa_em__lte=timezone.now()))
                    .order_by('pk').values_list('pk', flat=True)[:max(1, min(100, opts['limite']))])
                for pk in ids:
                    processar_evento(pk)
                if opts['once']:
                    return
                time.sleep(max(1, opts['intervalo']))
        except KeyboardInterrupt:
            return
