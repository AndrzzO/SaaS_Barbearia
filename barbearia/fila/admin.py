"""
Administração do Django para visualização operacional de apoio.
Guild Tech Support.
"""

from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.http import HttpResponse


class LoginProprietarioForm(AdminAuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            raise ValidationError('Usuário ou senha inválidos.', code='invalid_login')


class AdminProprietario(admin.AdminSite):
    site_header = 'Administração da Barbearia'
    site_title = 'Administração privada'
    login_form = LoginProprietarioForm

    def has_permission(self, request):
        return request.user.is_active and request.user.is_staff and request.user.is_superuser

    def login(self, request, extra_context=None):
        if request.method == 'POST':
            from fila.rate_limit import obter_ip_cliente, registrar_e_validar_tentativa_login, RateLimitExcedido
            try:
                registrar_e_validar_tentativa_login(obter_ip_cliente(request), escopo='admin_privado')
            except RateLimitExcedido as exc:
                resposta = HttpResponse('Limite de tentativas excedido. Tente novamente mais tarde.', status=429)
                resposta['Retry-After'] = str(exc.segundos_restantes)
                resposta['Cache-Control'] = 'no-store'
                return resposta
        return super().login(request, extra_context)


site_proprietario = AdminProprietario(name='admin')
site_proprietario.register(get_user_model(), UserAdmin)
site_proprietario.register(Group, GroupAdmin)

from fila.models import Barbeiro, ParticipacaoFila, RegistroTentativaLoginIP


@admin.register(Barbeiro, site=site_proprietario)
class BarbeiroAdmin(admin.ModelAdmin):
    list_display = ('nome_exibicao', 'usuario', 'ativo', 'online', 'proxima_ordem_fila', 'criado_em')
    list_filter = ('ativo', 'online')
    search_fields = ('nome_exibicao', 'usuario__username')
    readonly_fields = ('criado_em', 'atualizado_em')


@admin.register(ParticipacaoFila, site=site_proprietario)
class ParticipacaoFilaAdmin(admin.ModelAdmin):
    list_display = ('nome_completo', 'barbeiro', 'status', 'ordem_fila', 'ultrapassagens_por_falta', 'entrou_na_fila_em')
    list_filter = ('status', 'barbeiro', 'aguardando_ultrapassagem')
    search_fields = ('primeiro_nome', 'sobrenome', 'telefone')
    readonly_fields = ('identificador_publico', 'entrou_na_fila_em', 'hash_token_acesso')


@admin.register(RegistroTentativaLoginIP, site=site_proprietario)
class RegistroTentativaLoginIPAdmin(admin.ModelAdmin):
    list_display = ('ip', 'escopo', 'tentativas', 'primeira_tentativa_em', 'ultima_tentativa_em', 'bloqueado_ate')
    list_filter = ('escopo',)
    search_fields = ('ip',)
    readonly_fields = ('primeira_tentativa_em', 'ultima_tentativa_em')
