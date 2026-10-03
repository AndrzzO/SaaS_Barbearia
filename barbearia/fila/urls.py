"""
Mapeamento de rotas e endpoints do aplicativo Fila.
Guild Tech Support.
"""

from django.urls import path
from fila import views, paginas

urlpatterns = [
    path('', paginas.aplicativo, name='home'),
    path('cadastro/<int:barbeiro_id>/', paginas.aplicativo, {'tela':'cadastro'}, name='cadastro'),
    path('minha-fila/', paginas.aplicativo, {'tela':'acompanhamento'}, name='minha_fila'),
    path('acesso/barbeiro/', paginas.aplicativo, {'tela':'login_barbeiro'}),
    path('acesso/gestao/', paginas.aplicativo, {'tela':'login_gestao'}),
    path('barbeiro/', paginas.aplicativo, {'tela':'painel'}),
    path('barbeiro/perfil/', paginas.aplicativo, {'tela':'perfil'}),
    path('gestao/', paginas.aplicativo, {'tela':'gestao'}),
    path('gestao/equipe/', paginas.aplicativo, {'tela':'equipe'}),
    path('gestao/clientes/', paginas.aplicativo, {'tela':'clientes'}),
    path('gestao/novo-barbeiro/', paginas.aplicativo, {'tela':'novo_barbeiro'}),
    path('gestao/barbeiro/<int:barbeiro_id>/', paginas.aplicativo, {'tela':'gerenciar'}),
    path('manifest.webmanifest', paginas.manifesto),
    path('sw.js', paginas.service_worker),
    # 1. Rotas públicas
    path('api/publico/cadeiras/', views.home_cadeiras_publicas, name='publico_cadeiras'),
    path('api/publico/cadeiras/<int:barbeiro_id>/entrar/', views.cliente_entrar_na_fila, name='publico_entrar_fila'),

    # 2. Rotas do cliente temporário (via cookie)
    path('api/cliente/acompanhamento/', views.cliente_acompanhamento, name='cliente_acompanhamento'),
    path('api/cliente/sair/', views.cliente_sair_da_fila, name='cliente_sair_fila'),

    # 3. Rotas do barbeiro (autenticado)
    path('api/barbeiro/auth/login/', views.barbeiro_login, name='barbeiro_login'),
    path('api/barbeiro/auth/logout/', views.barbeiro_logout, name='barbeiro_logout'),
    path('api/barbeiro/painel/', views.barbeiro_painel_operacional, name='barbeiro_painel'),
    path('api/barbeiro/cadeira/status/', views.barbeiro_alterar_status_cadeira, name='barbeiro_status_cadeira'),
    path('api/barbeiro/fila/chamar-proximo/', views.barbeiro_chamar_proximo, name='barbeiro_chamar_proximo'),
    path('api/barbeiro/fila/chamar-especifico/<int:participacao_id>/', views.barbeiro_chamar_especifico, name='barbeiro_chamar_especifico'),
    path('api/barbeiro/fila/iniciar-atendimento/<int:participacao_id>/', views.barbeiro_iniciar_atendimento, name='barbeiro_iniciar_atendimento'),
    path('api/barbeiro/fila/finalizar-corte/<int:participacao_id>/', views.barbeiro_finalizar_corte, name='barbeiro_finalizar_corte'),
    path('api/barbeiro/fila/remover/<int:participacao_id>/', views.barbeiro_remover_participante, name='barbeiro_remover_participante'),

    # 4. Rotas do proprietário (superusuário ativo)
    path('api/proprietario/auth/login/', views.proprietario_login, name='proprietario_login'),
    path('api/proprietario/auth/logout/', views.proprietario_logout, name='proprietario_logout'),
    path('api/proprietario/visao-geral/', views.proprietario_visao_geral, name='proprietario_visao_geral'),
    path('api/proprietario/barbeiros/', views.proprietario_listar_barbeiros, name='proprietario_listar_barbeiros'),
    path('api/proprietario/barbeiros/cadastrar/', views.proprietario_cadastrar_barbeiro, name='proprietario_cadastrar_barbeiro'),
    path('api/proprietario/barbeiros/<int:barbeiro_id>/editar/', views.proprietario_editar_barbeiro, name='proprietario_editar_barbeiro'),
    path('api/proprietario/barbeiros/<int:barbeiro_id>/redefinir-senha/', views.proprietario_redefinir_senha_barbeiro, name='proprietario_redefinir_senha'),
    path('api/proprietario/barbeiros/<int:barbeiro_id>/status/', views.proprietario_alternar_status_barbeiro, name='proprietario_status_barbeiro'),
    path('api/proprietario/filas/', views.proprietario_filas_ativas, name='proprietario_filas_ativas'),
    path('api/proprietario/filas/remover/<int:participacao_id>/', views.proprietario_remover_participante, name='proprietario_remover_participante'),
]
