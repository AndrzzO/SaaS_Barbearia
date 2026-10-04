from django.contrib import admin
from fila.admin import site_proprietario
from .models import Barbearia, VinculoBarbearia, Assinatura, TentativaContratacao, ClienteCobranca, EventoStripe, Plano, AcaoPlataforma


class ConsultaAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Barbearia, site=site_proprietario)
class BarbeariaAdmin(ConsultaAdmin):
    list_display = ['nome', 'slug', 'provisionamento', 'situacao_administrativa', 'sincronizado_em', 'erro_integracao']
    list_filter = ['provisionamento', 'situacao_administrativa']
    search_fields = ['nome', 'slug']


@admin.register(Assinatura, site=site_proprietario)
class AssinaturaAdmin(ConsultaAdmin):
    list_display = ['barbearia', 'ambiente', 'status_stripe', 'acesso_pago_ate', 'cancelar_ao_final_do_periodo']


@admin.register(EventoStripe, site=site_proprietario)
class EventoAdmin(ConsultaAdmin):
    list_display = ['stripe_event_id', 'tipo', 'situacao', 'tentativas', 'erro_tecnico']
    list_filter = ['situacao', 'ambiente']


for model in [VinculoBarbearia, TentativaContratacao, ClienteCobranca, Plano, AcaoPlataforma]:
    site_proprietario.register(model, ConsultaAdmin)
