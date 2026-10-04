"""Pré-condição comercial dos testes operacionais herdados; sem bypass de acesso."""
from datetime import timedelta
from uuid import UUID
from django.contrib.auth import get_user_model
from django.utils import timezone
from allauth.socialaccount.models import SocialAccount
from saas.models import Barbearia, Plano, Assinatura, VinculoBarbearia

TENANT_ID = UUID('00000000-0000-0000-0000-000000000001')


def preparar_tenant():
    b, _ = Barbearia.objects.get_or_create(pk=TENANT_ID, defaults={'nome': 'Teste', 'slug': 'teste', 'provisionamento': 'PRONTO'})
    plano, _ = Plano.objects.get_or_create(codigo='mensal')
    Assinatura.objects.get_or_create(barbearia=b, ambiente='test', stripe_subscription_id='sub_fixture',
        defaults={'plano': plano, 'status_stripe': 'active', 'acesso_pago_desde': timezone.now() - timedelta(days=1),
                  'acesso_pago_ate': timezone.now() + timedelta(days=31)})
    return b


def criar_dono(*args, **kwargs):
    u = get_user_model().objects.create_user(*args, **kwargs)
    SocialAccount.objects.create(user=u, provider='google', uid='google-' + str(u.pk))
    VinculoBarbearia.objects.create(usuario=u, barbearia_id=TENANT_ID)
    return u
