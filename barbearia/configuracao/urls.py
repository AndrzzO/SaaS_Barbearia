"""
Configuração global de URLs do projeto Barbearia (Guild Tech Support).
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('', include('fila.urls')),
]

from django.conf import settings
from fila.admin import site_proprietario

if settings.ADMIN_SECRET_PATH:
    urlpatterns.insert(0, path(f'{settings.ADMIN_SECRET_PATH}/', site_proprietario.urls))

from django.conf.urls.static import static
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
