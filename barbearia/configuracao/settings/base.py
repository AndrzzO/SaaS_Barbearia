"""
Configurações base para o sistema de barbearia (Guild Tech Support).
Backend 100% Python/Django/SQLite.
"""

from pathlib import Path
import os
from dotenv import load_dotenv

# Diretório raiz do projeto (onde está o manage.py)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Carrega variáveis do .env se existir
load_dotenv(BASE_DIR / '.env')

# Segurança
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-dev-barbearia-guild-tech-2026')

# Em base/produção, DEBUG é False por padrão
DEBUG = False

# Hosts permitidos
allowed_hosts_raw = os.environ.get('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost')
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_raw.split(',') if host.strip()]

# Aplicações instaladas
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Aplicativo de negócio da fila
    'fila.apps.FilaConfig',
]

# Middlewares nativos
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'configuracao.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'configuracao.wsgi.application'
ASGI_APPLICATION = 'configuracao.asgi.application'

# Banco de dados SQLite persistente local
SQLITE_TIMEOUT = int(os.environ.get('SQLITE_TIMEOUT', 20))
SQLITE_ENABLE_WAL = os.environ.get('SQLITE_ENABLE_WAL', 'False').lower() in ('1', 'true', 'yes')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'TEST': {'NAME': BASE_DIR / 'test_fila.sqlite3'},
        'OPTIONS': {
            'transaction_mode': 'IMMEDIATE',
            'timeout': SQLITE_TIMEOUT,
        },
    }
}

# Validadores de senha nativos do Django
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internacionalização e fuso horário
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

# Arquivos estáticos
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Configurações de cookies e segurança de sessão
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False  # Permitir leitura por frontend para envio de header CSRF
CSRF_COOKIE_SAMESITE = 'Lax'

# Cookie de acesso temporário do cliente da fila
FILA_COOKIE_NOME = 'token_acesso_fila'
FILA_COOKIE_HTTPONLY = True
FILA_COOKIE_SAMESITE = 'Lax'
FILA_COOKIE_SECURE = False  # Sobrescrito para True em prod

# Parâmetros de Rate Limiting para login
RATE_LIMIT_LOGIN_MAX_TENTATIVAS = int(os.environ.get('RATE_LIMIT_LOGIN_MAX_TENTATIVAS', 30))
RATE_LIMIT_LOGIN_JANELA_MINUTOS = int(os.environ.get('RATE_LIMIT_LOGIN_JANELA_MINUTOS', 10))
RATE_LIMIT_LOGIN_BLOQUEIO_MINUTOS = int(os.environ.get('RATE_LIMIT_LOGIN_BLOQUEIO_MINUTOS', 30))

# Lista de IPs de proxies confiáveis (vazio = conexão direta)
trusted_proxies_raw = os.environ.get('TRUSTED_PROXY_IPS', '')
TRUSTED_PROXY_IPS = [ip.strip() for ip in trusted_proxies_raw.split(',') if ip.strip()]

# Permite banco isolado para validação sem alterar os dados locais.
if os.environ.get("SQLITE_PATH"):
    DATABASES["default"]["NAME"] = os.environ["SQLITE_PATH"]

# Endereço privado do admin: definido localmente, nunca publicado nos templates.
ADMIN_SECRET_PATH = os.environ.get('DJANGO_ADMIN_SECRET_PATH', '')
if ADMIN_SECRET_PATH:
    import re
    from django.core.exceptions import ImproperlyConfigured
    if not re.fullmatch(r'[A-Za-z0-9]{8}', ADMIN_SECRET_PATH):
        raise ImproperlyConfigured('DJANGO_ADMIN_SECRET_PATH deve conter oito letras/números.')
MIDDLEWARE.insert(0, 'fila.middleware.OcultarRotas404Middleware')

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# SaaS: contas permanentes, Google OAuth e cobrança isolada por ambiente.
INSTALLED_APPS += ['saas', 'allauth', 'allauth.account', 'allauth.socialaccount',
                   'allauth.socialaccount.providers.google']
MIDDLEWARE += ['allauth.account.middleware.AccountMiddleware', 'saas.middleware.TenantMiddleware']
AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend',
                           'allauth.account.auth_backends.AuthenticationBackend']
ACCOUNT_ADAPTER = 'saas.adapters.ContaAdapter'
SOCIALACCOUNT_ADAPTER = 'saas.adapters.GoogleAdapter'
SOCIALACCOUNT_ONLY = True
ACCOUNT_EMAIL_VERIFICATION = 'none'
ACCOUNT_UNIQUE_EMAIL = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_LOGIN_ON_GET = False
LOGIN_REDIRECT_URL = '/conta/'
LOGOUT_REDIRECT_URL = '/'
SOCIALACCOUNT_PROVIDERS = {'google': {
    'SCOPE': ['openid', 'email', 'profile'], 'AUTH_PARAMS': {'prompt': 'select_account'},
    'OAUTH_PKCE_ENABLED': True,
    'APP': {'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
            'secret': os.environ.get('GOOGLE_CLIENT_SECRET', ''), 'key': ''},
}}
SAAS_BASE_URL = os.environ.get('SAAS_BASE_URL', 'http://127.0.0.1:8000')
SAAS_TOLERANCIA_DIAS = int(os.environ.get('SAAS_TOLERANCIA_DIAS', '3'))
STRIPE_AMBIENTE = os.environ.get('STRIPE_AMBIENTE', 'test')
if STRIPE_AMBIENTE not in ('test', 'live'):
    raise ValueError('STRIPE_AMBIENTE deve ser test ou live')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
STRIPE_PORTAL_CONFIGURATION_ID = os.environ.get('STRIPE_PORTAL_CONFIGURATION_ID', '')
STRIPE_MAX_TENTATIVAS = 8
