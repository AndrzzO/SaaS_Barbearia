from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db.models import F
from django.utils import timezone
from saas.models import Barbearia
from saas.billing import sincronizar_empresa, erro_seguro
from saas.acesso import aplicar_suspensao


class Command(BaseCommand):
    help = 'Reconciliação limitada por lote; execute periodicamente. Menos recentes primeiro.'

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=25)
        parser.add_argument('--barbearia', default=None)

    def handle(self, *args, **opts):
        call_command('processar_cobranca', once=True, limite=opts['limite'])
        qs = Barbearia.objects.all()
        if opts['barbearia']:
            qs = qs.filter(pk=opts['barbearia'])
        ids = list(qs.order_by(F('sincronizado_em').asc(nulls_first=True), 'pk').values_list('pk', flat=True)[:max(1, min(100, opts['limite']))])
        for pk in ids:
            try:
                aplicar_suspensao(pk)  # Mesmo durante indisponibilidade da Stripe.
                sincronizar_empresa(pk)
            except Exception as exc:
                Barbearia.objects.filter(pk=pk).update(erro_integracao=erro_seguro(exc), sincronizado_em=timezone.now())
                self.stderr.write(f'{pk}: {erro_seguro(exc)}')
