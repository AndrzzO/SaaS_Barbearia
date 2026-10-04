"""Interface mobile e recursos PWA. Dados privados permanecem nas APIs."""
from pathlib import Path
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

@require_GET
@ensure_csrf_cookie
def aplicativo(request, tela='home', barbeiro_id=None):
    # Contrato temporário para integração do futuro frontend.
    from saas.acesso import dono
    if tela in ('painel', 'perfil'):
        if not (request.user.is_authenticated and request.user.is_active and hasattr(request.user, 'barbeiro')
                and request.user.barbeiro.ativo and request.user.barbeiro.barbearia_id == request.barbearia.pk):
            return JsonResponse({'erro': 'Autenticação operacional necessária.'}, status=403)
    if tela in ('gestao', 'equipe', 'clientes', 'gerenciar', 'novo_barbeiro') and not dono(request.user, request.barbearia.pk):
        return JsonResponse({'erro': 'Dono autorizado necessário.'}, status=403)
    resposta = JsonResponse({'tela': tela, 'barbearia': request.barbearia.slug, 'barbeiro_id': barbeiro_id,
                            'api_base': request.tenant_prefix + '/api/'})
    resposta['Cache-Control'] = 'no-store'
    return resposta

@require_GET
@ensure_csrf_cookie
def acesso_equipe(request):
    return JsonResponse({'login_url': request.tenant_prefix + '/api/barbeiro/auth/login/'})

@require_GET
def manifesto(request):
    return JsonResponse({'id':request.tenant_prefix + '/', 'name':'Barbearia • Guild Tech Support', 'short_name':'Barbearia',
        'lang':'pt-BR', 'start_url':request.tenant_prefix + '/', 'scope':request.tenant_prefix + '/', 'display':'standalone',
        'background_color':'#fbf2e3', 'theme_color':'#103542',
        'icons':[{'src':f'/static/fila/icone-{n}.png','sizes':f'{n}x{n}','type':'image/png','purpose':'any maskable'} for n in (192,512)]
    }, content_type='application/manifest+json')

@require_GET
def service_worker(request):
    resposta=HttpResponse((Path(__file__).parent/'static/fila/sw.js').read_text(encoding='utf-8'),content_type='application/javascript')
    resposta['Cache-Control']='no-cache'
    resposta['Service-Worker-Allowed']='/'
    return resposta
