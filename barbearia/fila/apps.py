"""
Configuração do aplicativo Fila.
Guild Tech Support.
"""

from django.apps import AppConfig
from django.db.backends.signals import connection_created


def configurar_sqlite(sender, connection, **kwargs):
    """
    Configura pragmas de desempenho e integridade para conexões SQLite.
    WAL mode, sincronização e integridade referencial.
    """
    if connection.vendor == 'sqlite':
        from django.conf import settings
        enable_wal = getattr(settings, 'SQLITE_ENABLE_WAL', True)
        with connection.cursor() as cursor:
            if enable_wal:
                cursor.execute("PRAGMA journal_mode=WAL;")
                modo = cursor.fetchone()[0]
                if modo.lower() != 'wal' and not connection.is_in_memory_db():
                    from django.core.exceptions import ImproperlyConfigured
                    raise ImproperlyConfigured("SQLite não conseguiu habilitar WAL.")
                cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")


class FilaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'fila'
    verbose_name = 'Fila da Barbearia'

    def ready(self):
        connection_created.connect(configurar_sqlite)
