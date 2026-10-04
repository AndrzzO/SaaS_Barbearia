"""Páginas GTS: apresentação fiel às referências e adaptação dos fluxos existentes."""
import json
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from allauth.socialaccount.providers.google.views import oauth2_login
from . import views

ERROS = {
    'STRIPE_NAO_CONFIGURADA': 'A contratação está sendo preparada. Tente novamente em breve.',
    'STRIPE_INDISPONIVEL': 'Não foi possível conectar ao pagamento. Tente novamente em instantes.',
    'INTEGRACAO_EM_ANDAMENTO': 'Sua contratação está sendo processada. Aguarde e tente novamente.',
    'ASSINATURA_EXISTENTE_USE_PORTAL': 'Você já tem uma assinatura. Acesse sua conta para gerenciá-la.',
}

def pagina(request, template, contexto=None, status=200):
    resposta = render(request, template, contexto or {}, status=status)
    resposta['Cache-Control'] = 'no-store'
    return resposta

@require_GET
@ensure_csrf_cookie
def entrar(request):
    return pagina(request, 'saas/auth.html', {'cadastro': False})

@require_GET
@ensure_csrf_cookie
def cadastro(request):
    return pagina(request, 'saas/auth.html', {'cadastro': True})

@require_POST
def google(request):
    app = settings.SOCIALACCOUNT_PROVIDERS.get('google', {}).get('APP', {})
    if not app.get('client_id') or not app.get('secret'):
        return pagina(request, 'saas/auth.html', {
            'cadastro': request.POST.get('cadastro') == '1',
            'erro_login': 'O acesso com Google está sendo preparado. Tente novamente em breve.',
        }, status=503)
    # O allauth continua responsável por OAuth, PKCE, state e validação de identidade.
    request.POST = request.POST.copy()
    request.POST['next'] = reverse('saas_painel')
    request.GET = request.GET.copy()
    request.GET['next'] = reverse('saas_painel')
    return oauth2_login(request)

@require_GET
@ensure_csrf_cookie
def planos(request):
    contexto = {}
    if request.user.is_authenticated:
        result = views.conta(request)
        if result.status_code == 200:
            estado = json.loads(result.content)
            contexto['gerenciar'] = not estado.get('pode_contratar', True) and estado['estado'] not in ('CADASTRO_BARBEARIA', 'CHECKOUT_PENDENTE')
            contexto['retomar_checkout'] = estado['estado'] == 'CHECKOUT_PENDENTE'
    return pagina(request, 'saas/plano.html', contexto)

@require_GET
@ensure_csrf_cookie
def painel(request):
    if not request.user.is_authenticated:
        return redirect('saas_login')
    resposta = views.conta(request)
    data = json.loads(resposta.content)
    return pagina(request, 'saas/painel.html', {'conta': data, 'estado_json': data}, status=resposta.status_code)

@require_POST
def assinar(request):
    is_json = 'application/json' in request.headers.get('Accept', '')
    def destino(nome):
        url = reverse(nome)
        return JsonResponse({'redirecionar': url}) if is_json else redirect(url)
    if not request.user.is_authenticated:
        return destino('saas_cadastro')
    from .models import VinculoBarbearia
    if not VinculoBarbearia.objects.filter(usuario=request.user, ativo=True).exists():
        return destino('saas_painel')
    resposta = views.checkout(request)
    data = json.loads(resposta.content)
    if is_json:
        if resposta.status_code >= 400:
            data['mensagem'] = ERROS.get(data.get('erro'), 'Não foi possível concluir agora. Consulte sua conta e tente novamente.')
            return JsonResponse(data, status=resposta.status_code)
        return resposta
    if resposta.status_code == 200:
        return redirect(data['url'])
    return pagina(request, 'saas/plano.html', {'erro': ERROS.get(data.get('erro'), 'Não foi possível concluir agora. Consulte sua conta e tente novamente.')}, status=resposta.status_code)
