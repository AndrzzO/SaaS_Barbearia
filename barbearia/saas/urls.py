from django.urls import path
from . import views, paginas

urlpatterns = [
    path('', views.comercial, name='saas_inicio'),
    path('entrar/', paginas.entrar, name='saas_login'),
    path('criar-conta/', paginas.cadastro, name='saas_cadastro'),
    path('planos/', paginas.planos, name='saas_planos'),
    path('minha-conta/', paginas.painel, name='saas_painel'),
    path('acessar-google/', paginas.google, name='saas_google'),
    path('assinar/', paginas.assinar, name='saas_assinar'),
    path('conta/', views.conta),
    path('conta/retorno/', views.conta),
    path('conta/barbearia/', views.cadastrar_barbearia),
    path('conta/checkout/', views.checkout),
    path('conta/portal/', views.portal),
    path('conta/cancelar/', views.cancelar),
    path('conta/retomar/', views.retomar),
    path('stripe/webhook/', views.webhook),
    path('plataforma/', views.plataforma),
    path('plataforma/empresas/<uuid:barbearia_id>/situacao/', views.bloquear),
    path('plataforma/empresas/<uuid:barbearia_id>/sincronizar/', views.sincronizar),
    path('plataforma/eventos/<int:evento_id>/reprocessar/', views.reprocessar),
]
