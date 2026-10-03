"""
Configuração global de URLs do projeto Barbearia (Guild Tech Support).
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('', include('fila.urls')),
]
