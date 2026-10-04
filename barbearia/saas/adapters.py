"""Google via allauth; provider + uid (Google sub), sem associação por e-mail."""
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from django.http import JsonResponse


class ContaAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return False  # Cadastro local de dono desabilitado; somente fluxo Google.

    def get_login_redirect_url(self, request):
        return '/minha-conta/'


class GoogleAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return sociallogin.account.provider == 'google'

    def pre_social_login(self, request, sociallogin):
        user = sociallogin.user
        if (sociallogin.account.provider != 'google' or not sociallogin.account.uid
                or sociallogin.state.get('process') == 'connect'
                or (request.user.is_authenticated and (request.user.is_staff or hasattr(request.user, 'barbeiro')))
                or (user.pk and (user.is_staff or user.is_superuser or hasattr(user, 'barbeiro')))):
            raise ImmediateHttpResponse(JsonResponse({'erro': 'Identidade incompatível com conta de dono.'}, status=403))

    def save_user(self, request, sociallogin, form=None):
        sociallogin.user.is_staff = False
        sociallogin.user.is_superuser = False
        user = super().save_user(request, sociallogin, form)
        user.set_unusable_password()
        user.save(update_fields=['password'])
        return user
