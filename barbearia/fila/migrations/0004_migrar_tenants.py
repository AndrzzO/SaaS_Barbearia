from django.db import migrations, models
import django.db.models.deletion


def migrar(apps, schema_editor):
    Barbeiro = apps.get_model('fila', 'Barbeiro')
    Participacao = apps.get_model('fila', 'ParticipacaoFila')
    Barbearia = apps.get_model('saas', 'Barbearia')
    Plano = apps.get_model('saas', 'Plano')
    Plano.objects.get_or_create(codigo='mensal')
    if Barbeiro.objects.filter(barbearia__isnull=True).exists() or Participacao.objects.filter(barbearia__isnull=True).exists():
        b = Barbearia.objects.create(nome='Barbearia migrada', slug='barbearia-inicial', provisionamento='CONFIGURACAO')
        Barbeiro.objects.filter(barbearia__isnull=True).update(barbearia=b, online=False)
        for p in Participacao.objects.filter(barbearia__isnull=True).select_related('barbeiro').iterator():
            p.barbearia_id = p.barbeiro.barbearia_id
            p.save(update_fields=['barbearia'])
    for p in Participacao.objects.select_related('barbeiro').iterator():
        if p.barbearia_id != p.barbeiro.barbearia_id:
            raise RuntimeError('Vínculo operacional inconsistente; restaure o backup e investigue.')
    # Superusuários herdados continuam exclusivamente administradores GTS.
    # O dono Google é associado explicitamente pelo comando vincular_dono_migrado.


class Migration(migrations.Migration):
    dependencies = [('fila', '0003_remove_participacaofila_unique_participacao_telefone_and_more')]
    operations = [
        migrations.RunPython(migrar, migrations.RunPython.noop),
        migrations.AlterField(model_name='barbeiro', name='barbearia', field=models.ForeignKey(
            on_delete=django.db.models.deletion.PROTECT, related_name='barbeiros', to='saas.barbearia')),
        migrations.AlterField(model_name='participacaofila', name='barbearia', field=models.ForeignKey(
            on_delete=django.db.models.deletion.PROTECT, related_name='participacoes', to='saas.barbearia')),
    ]
