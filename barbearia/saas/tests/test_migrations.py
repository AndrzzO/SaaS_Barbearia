from datetime import date
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MigracaoTenantTests(TransactionTestCase):
    def test_preserva_registros_e_nao_concede_assinatura(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        executor.migrate([('fila', '0002_barbeiro_foto')])
        try:
            apps = executor.loader.project_state([('fila', '0002_barbeiro_foto')]).apps
            u = apps.get_model('auth', 'User').objects.create(username='migrado')
            b = apps.get_model('fila', 'Barbeiro').objects.create(usuario_id=u.pk, nome_exibicao='Existente', online=True)
            p = apps.get_model('fila', 'ParticipacaoFila').objects.create(barbeiro_id=b.pk, primeiro_nome='Pessoa',
                sobrenome='Existente', telefone='11987654321', data_nascimento=date(1990, 1, 1), ordem_fila=1, hash_token_acesso='hash')
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from fila.models import Barbeiro, ParticipacaoFila
            from saas.models import Assinatura
            migrated = Barbeiro.objects.get(pk=b.pk)
            self.assertFalse(migrated.online)
            self.assertEqual(migrated.barbearia.slug, 'barbearia-inicial')
            self.assertEqual(ParticipacaoFila.objects.get(pk=p.pk).barbearia_id, migrated.barbearia_id)
            self.assertFalse(Assinatura.objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
