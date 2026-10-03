"""
Inicializador do módulo de configurações.
Padrão: dev se não especificado explicitamente.
"""

import os

settings_module = os.environ.get('DJANGO_SETTINGS_MODULE', 'configuracao.settings.dev')
if settings_module == 'configuracao.settings.prod':
    from .prod import *
else:
    from .dev import *
