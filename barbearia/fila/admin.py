"""
Administração do Django para visualização operacional de apoio.
Guild Tech Support.
"""

from django.contrib import admin
from fila.models import Barbeiro, ParticipacaoFila, RegistroTentativaLoginIP


@admin.register(Barbeiro)
class BarbeiroAdmin(admin.ModelAdmin):
    list_display = ('nome_exibicao', 'usuario', 'ativo', 'online', 'proxima_ordem_fila', 'criado_em')
    list_filter = ('ativo', 'online')
    search_fields = ('nome_exibicao', 'usuario__username')
    readonly_fields = ('criado_em', 'atualizado_em')


@admin.register(ParticipacaoFila)
class ParticipacaoFilaAdmin(admin.ModelAdmin):
    list_display = ('nome_completo', 'barbeiro', 'status', 'ordem_fila', 'ultrapassagens_por_falta', 'entrou_na_fila_em')
    list_filter = ('status', 'barbeiro', 'aguardando_ultrapassagem')
    search_fields = ('primeiro_nome', 'sobrenome', 'telefone')
    readonly_fields = ('identificador_publico', 'entrou_na_fila_em', 'hash_token_acesso')


@admin.register(RegistroTentativaLoginIP)
class RegistroTentativaLoginIPAdmin(admin.ModelAdmin):
    list_display = ('ip', 'escopo', 'tentativas', 'primeira_tentativa_em', 'ultima_tentativa_em', 'bloqueado_ate')
    list_filter = ('escopo',)
    search_fields = ('ip',)
    readonly_fields = ('primeira_tentativa_em', 'ultima_tentativa_em')
