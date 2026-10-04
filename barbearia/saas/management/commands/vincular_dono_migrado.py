from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from allauth.socialaccount.models import SocialAccount
from saas.models import Barbearia, VinculoBarbearia


class Command(BaseCommand):
    help = 'Associa explicitamente um dono Google à barbearia migrada; não concede pagamento.'

    def add_arguments(self, parser):
        parser.add_argument('--usuario-id', type=int, required=True)
        parser.add_argument('--slug', required=True)

    def handle(self, *args, **opts):
        with transaction.atomic():
            social = SocialAccount.objects.filter(user_id=opts['usuario_id'], provider='google').select_related('user').first()
            if not social or not social.user.is_active or social.user.is_staff or social.user.is_superuser or hasattr(social.user, 'barbeiro'):
                raise CommandError('Requer usuário Google comum e ativo; não associe por coincidência de e-mail.')
            b = Barbearia.objects.get(slug=opts['slug'])
            if b.vinculos.exists() or VinculoBarbearia.objects.filter(usuario=social.user, ativo=True).exists():
                raise CommandError('Empresa ou usuário já possui vínculo; revise antes de continuar.')
            VinculoBarbearia.objects.create(usuario=social.user, barbearia=b)
        self.stdout.write('Dono associado. Assinatura permanece necessária.')
