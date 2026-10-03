"""
Configurações para ambiente de produção (foco em segurança e HTTPS).
"""

from .base import *

DEBUG = False

# Cookies seguros em HTTPS
FILA_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Cabeçalhos de segurança
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Produção exige configuração externa explícita.
from django.core.exceptions import ImproperlyConfigured
if SECRET_KEY == 'django-insecure-dev-barbearia-guild-tech-2026' or len(SECRET_KEY) < 50:
    raise ImproperlyConfigured('Defina DJANGO_SECRET_KEY exclusiva com pelo menos 50 caracteres.')
if not os.environ.get('DJANGO_ALLOWED_HOSTS'):
    raise ImproperlyConfigured('Defina DJANGO_ALLOWED_HOSTS explicitamente.')
