"""
Configurações para ambiente de desenvolvimento local.
"""

from .base import *

DEBUG = True

# Cookies sem Secure obrigatório em desenvolvimento HTTP local
FILA_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
