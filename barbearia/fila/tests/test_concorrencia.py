"""
Testes de concorrência e integridade transacional no SQLite (Passo 17 e Passo 20).
Guild Tech Support.
Utiliza TransactionTestCase com conexões ativas reais.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import uuid

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from fila.models import Barbeiro, ParticipacaoFila, StatusParticipacao
from fila.services import (
    entrar_na_fila,
    iniciar_atendimento,
    finalizar_corte,
    remover_participante,
    processar_filas_servico,
    ConflitoEstadoError,
    DuplicidadeTelefoneError,
    NaoEncontradoError,
)

User = get_user_model()


class TestConcorrenciaSQLite(TransactionTestCase):
    """Testa contenção, corridas de transação e atomicidade no SQLite."""

    def setUp(self):
        self.user_barbeiro = User.objects.create_user(
            username="barbeiro_concorrencia",
            password="senha_concorrencia_123"
        )
        self.barbeiro = Barbeiro.objects.create(
            usuario=self.user_barbeiro,
            nome_exibicao="Barbeiro Concorrência",
            ativo=True,
            online=True,
            proxima_ordem_fila=1
        )

    def test_duas_entradas_simultaneas_mesmo_telefone(self):
        """Duas requisições simultâneas com o mesmo telefone: apenas uma pode ser criada."""
        resultados = []
        erros = []

        def _tentar_entrar(nome):
            # Garante que a thread feche sua conexão no final
            try:
                p, token, ja_existia = entrar_na_fila(
                    barbeiro_id=self.barbeiro.id,
                    primeiro_nome_raw=nome,
                    sobrenome_raw="Teste",
                    telefone_raw="(11) 98888-7777",
                    data_nascimento_input=date(1995, 1, 1)
                )
                resultados.append((p, ja_existia))
            except Exception as e:
                erros.append(e)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = [
                executor.submit(_tentar_entrar, "Primeiro"),
                executor.submit(_tentar_entrar, "Segundo"),
            ]
            for f in futuros:
                f.result()

        # Exatamente uma deve ter criado com sucesso e a outra deve ter sido rejeitada com DuplicidadeTelefoneError
        self.assertEqual(len(resultados), 1)
        self.assertEqual(len(erros), 1)
        self.assertIsInstance(erros[0], DuplicidadeTelefoneError)
        self.assertEqual(ParticipacaoFila.objects.filter(telefone='11988887777').count(), 1)

    def test_dois_inicios_concorrentes_na_mesma_chamada(self):
        """Duas requisições simultâneas para iniciar atendimento da mesma chamada."""
        p1, token1, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro.id,
            primeiro_nome_raw="Lucas",
            sobrenome_raw="Mendes",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1995, 4, 10)
        )
        identificador = str(p1.identificador_chamada)

        resultados = []
        erros = []

        def _tentar_iniciar():
            try:
                res = iniciar_atendimento(self.barbeiro, p1.id, identificador)
                resultados.append(res)
            except Exception as e:
                erros.append(e)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = [
                executor.submit(_tentar_iniciar),
                executor.submit(_tentar_iniciar),
            ]
            for f in futuros:
                f.result()

        # Exatamente um teve sucesso e o outro falhou com ConflitoEstadoError
        self.assertEqual(len(resultados), 1)
        self.assertEqual(len(erros), 1)
        self.assertIsInstance(erros[0], ConflitoEstadoError)

        p1.refresh_from_db()
        self.assertEqual(p1.status, StatusParticipacao.EM_ATENDIMENTO)

    def test_inicio_versus_expiracao_de_chamada(self):
        """Se a chamada expirou, tentar iniciar atendimento é rejeitado e ela é movida para o final."""
        p1, token1, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro.id,
            primeiro_nome_raw="Lucas",
            sobrenome_raw="Mendes",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1995, 4, 10)
        )
        identificador = str(p1.identificador_chamada)

        # Simula expiração
        p1.chamada_iniciada_em = timezone.now() - timedelta(seconds=40)
        p1.chamada_expira_em = timezone.now() - timedelta(seconds=10)
        p1.save(update_fields=['chamada_iniciada_em', 'chamada_expira_em'])

        # Barbeiro tenta iniciar atendimento após o prazo
        with self.assertRaises(ConflitoEstadoError):
            iniciar_atendimento(self.barbeiro, p1.id, identificador)

        p1.refresh_from_db()
        self.assertEqual(p1.status, StatusParticipacao.AGUARDANDO)
        self.assertTrue(p1.aguardando_ultrapassagem)

    def test_requisicao_antiga_ciclo_invalido(self):
        """Requisição atrasada com UUID de chamada anterior é rejeitada."""
        p1, token1, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro.id,
            primeiro_nome_raw="Lucas",
            sobrenome_raw="Mendes",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1995, 4, 10)
        )
        ciclo_antigo = str(uuid.uuid4())  # Ciclo diferente do atual

        with self.assertRaises(ConflitoEstadoError):
            iniciar_atendimento(self.barbeiro, p1.id, ciclo_antigo)

    def test_contagem_unica_de_ultrapassagem(self):
        """Verifica que a ultrapassagem é incrementada estritamente uma única vez por início de atendimento."""
        # P1 entra e tem chamada expirada
        p1, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro.id,
            primeiro_nome_raw="Lucas",
            sobrenome_raw="Faltoso",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1995, 4, 10)
        )
        # P2 entra
        p2, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro.id,
            primeiro_nome_raw="Marcos",
            sobrenome_raw="Presente",
            telefone_raw="(11) 98765-4322",
            data_nascimento_input=date(1994, 2, 10)
        )

        # P1 expira
        p1.chamada_iniciada_em = timezone.now() - timedelta(seconds=40)
        p1.chamada_expira_em = timezone.now() - timedelta(seconds=10)
        p1.save(update_fields=['chamada_iniciada_em', 'chamada_expira_em'])
        processar_filas_servico()

        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertTrue(p1.aguardando_ultrapassagem)
        self.assertEqual(p1.ultrapassagens_por_falta, 0)
        self.assertEqual(p2.status, StatusParticipacao.CHAMADO)

        # Inicia atendimento de P2
        iniciar_atendimento(self.barbeiro, p2.id, str(p2.identificador_chamada))

        p1.refresh_from_db()
        # Exatamente 1 ultrapassagem!
        self.assertEqual(p1.ultrapassagens_por_falta, 1)
        self.assertFalse(p1.aguardando_ultrapassagem)
