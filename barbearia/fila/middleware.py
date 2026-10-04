"""Não revela o catálogo de URLs em respostas 404, mesmo no desenvolvimento."""
from django.http import HttpResponse

class OcultarRotas404Middleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resposta = self.get_response(request)
        if resposta.status_code == 404:
            resposta = HttpResponse('<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Página não encontrada</title><h1>Página não encontrada</h1><p>O endereço solicitado não existe.</p></html>', status=404)
            resposta['Cache-Control'] = 'no-store'
        return resposta
