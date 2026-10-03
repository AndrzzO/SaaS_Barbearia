"""Interface mobile e recursos PWA. Dados privados permanecem nas APIs."""
from pathlib import Path
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

@require_GET
@ensure_csrf_cookie
def aplicativo(request, tela='home', barbeiro_id=None):
    if tela in ('painel', 'perfil'):
        if not (request.user.is_authenticated and request.user.is_active and hasattr(request.user, 'barbeiro') and request.user.barbeiro.ativo):
            return redirect('/acesso/barbeiro/')
    if tela in ('gestao', 'equipe', 'clientes', 'gerenciar', 'novo_barbeiro'):
        if not (request.user.is_authenticated and request.user.is_active and request.user.is_superuser):
            return redirect('/acesso/gestao/')
    resposta = render(request, 'fila/aplicativo.html', {'tela': tela, 'barbeiro_id': barbeiro_id})
    resposta['Cache-Control'] = 'no-store'
    return resposta

@require_GET
def manifesto(request):
    return JsonResponse({'id':'/', 'name':'Barbearia • Guild Tech Support', 'short_name':'Barbearia',
        'lang':'pt-BR', 'start_url':'/', 'scope':'/', 'display':'standalone',
        'background_color':'#fbf2e3', 'theme_color':'#103542',
        'icons':[{'src':f'/static/fila/icone-{n}.png','sizes':f'{n}x{n}','type':'image/png','purpose':'any maskable'} for n in (192,512)]
    }, content_type='application/manifest+json')

@require_GET
def service_worker(request):
    resposta=HttpResponse((Path(__file__).parent/'static/fila/sw.js').read_text(encoding='utf-8'),content_type='application/javascript')
    resposta['Cache-Control']='no-cache'
    resposta['Service-Worker-Allowed']='/'
    return resposta
