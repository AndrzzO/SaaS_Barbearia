from django.http import JsonResponse
from .models import Barbearia


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view, args, kwargs):
        slug = kwargs.pop('tenant_slug', None)
        if slug is None:
            return
        try:
            request.barbearia = Barbearia.objects.get(slug=slug)
        except Barbearia.DoesNotExist:
            return JsonResponse({'erro': 'Barbearia não encontrada.'}, status=404)
        # Não bloqueia consulta/remoção/finalização: serviço decide por operação.
        request.tenant_prefix = '/b/' + request.barbearia.slug
