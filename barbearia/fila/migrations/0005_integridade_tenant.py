from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('fila', '0004_migrar_tenants')]
    operations = [migrations.RunSQL(sql=[
        """CREATE TRIGGER fila_tenant_insert BEFORE INSERT ON fila_participacaofila
        WHEN NOT EXISTS (SELECT 1 FROM fila_barbeiro b WHERE b.id = NEW.barbeiro_id AND b.barbearia_id = NEW.barbearia_id)
        BEGIN SELECT RAISE(ABORT, 'participacao_tenant_incompativel'); END""",
        """CREATE TRIGGER fila_tenant_update BEFORE UPDATE OF barbeiro_id, barbearia_id ON fila_participacaofila
        WHEN NOT EXISTS (SELECT 1 FROM fila_barbeiro b WHERE b.id = NEW.barbeiro_id AND b.barbearia_id = NEW.barbearia_id)
        BEGIN SELECT RAISE(ABORT, 'participacao_tenant_incompativel'); END""",
        """CREATE TRIGGER cadeira_tenant_imutavel BEFORE UPDATE OF barbearia_id ON fila_barbeiro
        WHEN NEW.barbearia_id != OLD.barbearia_id
        BEGIN SELECT RAISE(ABORT, 'cadeira_tenant_imutavel'); END""",
    ], reverse_sql=['DROP TRIGGER fila_tenant_insert', 'DROP TRIGGER fila_tenant_update', 'DROP TRIGGER cadeira_tenant_imutavel'])]
