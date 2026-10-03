"""
Testes abrangentes da camada de serviços (regras de negócio, chamadas, faltas e ciclo de vida).
Guild Tech Support.
"""

from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from fila.models import Barbeiro, ParticipacaoFila, StatusParticipacao
from fila.services import (
    listar_cadeiras_publicas,
    entrar_na_fila,
    iniciar_atendimento,
    chamar_proximo_manual,
    chamar_especifico,
    finalizar_corte,
    sair_da_fila,
    remover_participante,
    alternar_status_cadeira,
    desativar_barbeiro,
    processar_filas_servico,
    buscar_participacao_por_token,
    ValidacaoFilaError,
    DuplicidadeTelefoneError,
    ConflitoEstadoError,
    NaoAutorizadoError,
)

User = get_user_model()


class TestServicosFila(TestCase):
    """Testes completos das regras operacionais da barbearia."""

    def setUp(self):
        self.user_proprietario = User.objects.create_superuser(
            username="dono",
            password="dono_senha_123"
        )
        self.user_barbeiro1 = User.objects.create_user(
            username="barbeiro1",
            password="barbeiro_senha_123"
        )
        self.barbeiro1 = Barbeiro.objects.create(
            usuario=self.user_barbeiro1,
            nome_exibicao="Cadeira 1 - Bruno",
            ativo=True,
            online=True,
            proxima_ordem_fila=1
        )

        self.user_barbeiro2 = User.objects.create_user(
            username="barbeiro2",
            password="barbeiro_senha_123"
        )
        self.barbeiro2 = Barbeiro.objects.create(
            usuario=self.user_barbeiro2,
            nome_exibicao="Cadeira 2 - André",
            ativo=True,
            online=False,
            proxima_ordem_fila=1
        )

    def test_listar_cadeiras_publicas(self):
        cadeiras = listar_cadeiras_publicas()
        self.assertEqual(len(cadeiras), 2)
        # Cadeira 1 está online e aceita entrada
        c1 = next(c for c in cadeiras if c['identificador_barbeiro'] == self.barbeiro1.id)
        self.assertTrue(c1['online'])
        self.assertTrue(c1['aceita_entrada'])
        self.assertEqual(c1['quantidade_aguardando'], 0)
        self.assertFalse(c1['atendimento_em_andamento'])

        # Cadeira 2 está offline e não aceita entrada
        c2 = next(c for c in cadeiras if c['identificador_barbeiro'] == self.barbeiro2.id)
        self.assertFalse(c2['online'])
        self.assertFalse(c2['aceita_entrada'])

    def test_entrada_na_fila_e_chamada_automatica(self):
        # Primeira entrada em cadeira online e livre: é chamado automaticamente!
        p1, token1, reapresentado = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Carlos",
            sobrenome_raw="Eduardo",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1998, 4, 15)
        )
        self.assertFalse(reapresentado)
        self.assertEqual(p1.status, StatusParticipacao.CHAMADO)
        self.assertIsNotNone(p1.chamada_iniciada_em)
        self.assertIsNotNone(p1.chamada_expira_em)
        self.assertIsNotNone(p1.identificador_chamada)
        self.assertEqual(p1.ordem_fila, 1)

        # Segunda entrada na mesma cadeira enquanto a primeira está chamada: fica AGUARDANDO
        p2, token2, reapresentado2 = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Mariana",
            sobrenome_raw="Costa",
            telefone_raw="(11) 98765-4322",
            data_nascimento_input=date(2000, 8, 20)
        )
        self.assertFalse(reapresentado2)
        self.assertEqual(p2.status, StatusParticipacao.AGUARDANDO)
        self.assertEqual(p2.ordem_fila, 2)

    def test_reapresentacao_com_token_valido(self):
        p1, token1, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Carlos",
            sobrenome_raw="Eduardo",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1998, 4, 15)
        )
        # Reenvio com o mesmo token recebido no cookie
        p_reapresentado, token_retornado, reapresentou = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Qualquer",
            sobrenome_raw="Nome",
            telefone_raw="(11) 99999-9999",
            data_nascimento_input=date(1990, 1, 1),
            token_cliente_atual=token1
        )
        self.assertTrue(reapresentou)
        self.assertEqual(p_reapresentado.pk, p1.pk)
        self.assertEqual(token_retornado, token1)

    def test_rejeicao_duplicidade_sem_token(self):
        p1, token1, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Carlos",
            sobrenome_raw="Eduardo",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1998, 4, 15)
        )
        # Outro navegador/solicitante tentando mesmo telefone sem o token
        with self.assertRaises(DuplicidadeTelefoneError):
            entrar_na_fila(
                barbeiro_id=self.barbeiro1.id,
                primeiro_nome_raw="Outro",
                sobrenome_raw="Tentando",
                telefone_raw="(11) 98765-4321",
                data_nascimento_input=date(1995, 1, 1),
                token_cliente_atual=None
            )

    def test_rejeicao_entrada_cadeira_offline(self):
        with self.assertRaises(ConflitoEstadoError):
            entrar_na_fila(
                barbeiro_id=self.barbeiro2.id,  # cadeira 2 está offline
                primeiro_nome_raw="Rafael",
                sobrenome_raw="Silveira",
                telefone_raw="(11) 98765-4323",
                data_nascimento_input=date(1992, 10, 5)
            )

    def test_expiracao_de_chamada_30s_e_reposicionamento(self):
        p1, token1, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Cliente",
            sobrenome_raw="Um",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1998, 4, 15)
        )
        p2, token2, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Cliente",
            sobrenome_raw="Dois",
            telefone_raw="(11) 98765-4322",
            data_nascimento_input=date(1995, 3, 20)
        )
        self.assertEqual(p1.status, StatusParticipacao.CHAMADO)
        self.assertEqual(p2.status, StatusParticipacao.AGUARDANDO)

        # Simula expiração: avança o tempo em p1 mantendo chamada_expira_em > chamada_iniciada_em
        p1.chamada_iniciada_em = timezone.now() - timedelta(seconds=40)
        p1.chamada_expira_em = timezone.now() - timedelta(seconds=10)
        p1.save(update_fields=['chamada_iniciada_em', 'chamada_expira_em'])

        # Processador de filas executa
        operacoes = processar_filas_servico()
        self.assertGreaterEqual(operacoes, 1)

        p1.refresh_from_db()
        p2.refresh_from_db()

        # p1 deve ter voltado para AGUARDANDO, com aguardando_ultrapassagem=True e ordem no final da fila
        self.assertEqual(p1.status, StatusParticipacao.AGUARDANDO)
        self.assertTrue(p1.aguardando_ultrapassagem)
        self.assertIsNone(p1.chamada_iniciada_em)
        self.assertIsNone(p1.identificador_chamada)
        self.assertEqual(p1.ultrapassagens_por_falta, 0)  # NÃO incrementa na expiração!

        # p2 agora foi chamado!
        self.assertEqual(p2.status, StatusParticipacao.CHAMADO)
        self.assertFalse(p2.aguardando_ultrapassagem)

    def test_regra_cinco_ultrapassagens_por_falta(self):
        """
        Testa rigorosamente a regra das 5 ultrapassagens:
        - Cliente 1 perde a chamada e fica aguardando_ultrapassagem=True;
        - Cliente 2 inicia atendimento: Cliente 1 recebe 1ª ultrapassagem e volta a aguardando_ultrapassagem=False;
        - Testar até a 5ª ultrapassagem, onde Cliente 1 é excluído definitivamente.
        """
        p1, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Cliente",
            sobrenome_raw="Faltoso",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1998, 4, 15)
        )
        self.assertEqual(p1.status, StatusParticipacao.CHAMADO)

        # 4 outros clientes entram na fila com nomes alfabéticos
        nomes_outros = ["Dois", "Tres", "Quatro", "Cinco", "Seis"]
        tokens_outros = []
        for i, nome_sufixo in enumerate(nomes_outros, start=2):
            p, tok, _ = entrar_na_fila(
                barbeiro_id=self.barbeiro1.id,
                primeiro_nome_raw=f"Outro{nome_sufixo}",
                sobrenome_raw="Teste",
                telefone_raw=f"(11) 98765-432{i}",
                data_nascimento_input=date(1990, 1, 1)
            )
            tokens_outros.append(p)

        # 1ª Falta de P1 mantendo expira > inicio
        p1.chamada_iniciada_em = timezone.now() - timedelta(seconds=40)
        p1.chamada_expira_em = timezone.now() - timedelta(seconds=10)
        p1.save(update_fields=['chamada_iniciada_em', 'chamada_expira_em'])
        processar_filas_servico()

        p1.refresh_from_db()
        self.assertTrue(p1.aguardando_ultrapassagem)
        self.assertEqual(p1.ultrapassagens_por_falta, 0)

        # Agora Outro2 é chamado automaticamente
        outro2 = ParticipacaoFila.objects.get(pk=tokens_outros[0].pk)
        self.assertEqual(outro2.status, StatusParticipacao.CHAMADO)

        # Inicia atendimento de Outro2 -> deve causar a 1ª ultrapassagem em P1
        iniciar_atendimento(self.barbeiro1, outro2.pk, str(outro2.identificador_chamada))

        p1.refresh_from_db()
        self.assertEqual(p1.ultrapassagens_por_falta, 1)
        # Regra: se < 5, volta aguardando_ultrapassagem=False (reabilita para nova chamada)
        self.assertFalse(p1.aguardando_ultrapassagem)

        # Finaliza Outro2
        finalizar_corte(self.barbeiro1, outro2.pk)

        # Agora, para testar as ultrapassagens subsequentes de 2 a 5:
        # Simulamos que P1 perde chamadas sucessivas
        for falta in range(2, 6):
            p1.refresh_from_db()
            # P1 perde a chamada novamente:
            p1.aguardando_ultrapassagem = True
            p1.save(update_fields=['aguardando_ultrapassagem'])

            # Próximo cliente é chamado
            proximo_cliente = ParticipacaoFila.objects.filter(
                barbeiro=self.barbeiro1, status=StatusParticipacao.AGUARDANDO
            ).exclude(pk=p1.pk).first()
            if not proximo_cliente:
                # Cria novo se necessário
                mapa_nomes = {2: "Dois", 3: "Tres", 4: "Quatro", 5: "Cinco"}
                proximo_cliente, _, _ = entrar_na_fila(
                    barbeiro_id=self.barbeiro1.id,
                    primeiro_nome_raw=f"Novo{mapa_nomes[falta]}",
                    sobrenome_raw="Teste",
                    telefone_raw=f"(11) 98765-433{falta}",
                    data_nascimento_input=date(1990, 1, 1)
                )

            # Chama proximo_cliente se não chamado
            if proximo_cliente.status != StatusParticipacao.CHAMADO:
                proximo_cliente = chamar_especifico(self.barbeiro1, proximo_cliente.pk)

            # Inicia atendimento do proximo_cliente -> incrementa falta em P1
            iniciar_atendimento(self.barbeiro1, proximo_cliente.pk, str(proximo_cliente.identificador_chamada))

            if falta < 5:
                p1.refresh_from_db()
                self.assertEqual(p1.ultrapassagens_por_falta, falta)
                self.assertFalse(p1.aguardando_ultrapassagem)
                finalizar_corte(self.barbeiro1, proximo_cliente.pk)
            else:
                # Na 5ª falta/ultrapassagem: exclusão imediata!
                self.assertFalse(ParticipacaoFila.objects.filter(pk=p1.pk).exists())

    def test_troca_de_chamada_sem_penalidade_pelo_barbeiro(self):
        p1, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Primeiro",
            sobrenome_raw="Um",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1998, 4, 15)
        )
        p2, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Segundo",
            sobrenome_raw="Dois",
            telefone_raw="(11) 98765-4322",
            data_nascimento_input=date(1995, 3, 20)
        )
        self.assertEqual(p1.status, StatusParticipacao.CHAMADO)
        self.assertEqual(p2.status, StatusParticipacao.AGUARDANDO)

        ordem_original_p1 = p1.ordem_fila

        # Barbeiro decide chamar p2 enquanto p1 ainda está na chamada (vigente)
        chamar_especifico(self.barbeiro1, p2.pk)

        p1.refresh_from_db()
        p2.refresh_from_db()

        # p2 agora é CHAMADO
        self.assertEqual(p2.status, StatusParticipacao.CHAMADO)

        # p1 retornou à espera sem penalidade, sem aguardando_ultrapassagem e mantendo sua ordem original
        self.assertEqual(p1.status, StatusParticipacao.AGUARDANDO)
        self.assertEqual(p1.ordem_fila, ordem_original_p1)
        self.assertFalse(p1.aguardando_ultrapassagem)
        self.assertEqual(p1.ultrapassagens_por_falta, 0)
        self.assertIsNone(p1.identificador_chamada)

    def test_cliente_sair_da_fila(self):
        p1, token1, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Primeiro",
            sobrenome_raw="Um",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1998, 4, 15)
        )
        p2, token2, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Segundo",
            sobrenome_raw="Dois",
            telefone_raw="(11) 98765-4322",
            data_nascimento_input=date(1995, 3, 20)
        )

        # p1 sai da fila voluntariamente
        sair_da_fila(p1)
        self.assertFalse(ParticipacaoFila.objects.filter(pk=p1.pk).exists())

        # p2 que estava AGUARDANDO é chamado automaticamente
        p2.refresh_from_db()
        self.assertEqual(p2.status, StatusParticipacao.CHAMADO)

    def test_cliente_nao_pode_sair_em_atendimento(self):
        p1, token1, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Primeiro",
            sobrenome_raw="Um",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1998, 4, 15)
        )
        iniciar_atendimento(self.barbeiro1, p1.pk, str(p1.identificador_chamada))
        p1.refresh_from_db()
        self.assertEqual(p1.status, StatusParticipacao.EM_ATENDIMENTO)

        with self.assertRaises(ConflitoEstadoError):
            sair_da_fila(p1)

    def test_desligar_cadeira_limpa_espera_mas_preserva_atendimento(self):
        # p1 entra e inicia atendimento
        p1, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="EmCorte",
            sobrenome_raw="Um",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1998, 4, 15)
        )
        iniciar_atendimento(self.barbeiro1, p1.pk, str(p1.identificador_chamada))

        # p2 e p3 entram na fila
        p2, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Espera",
            sobrenome_raw="Dois",
            telefone_raw="(11) 98765-4322",
            data_nascimento_input=date(1995, 3, 20)
        )
        p3, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="Espera",
            sobrenome_raw="Tres",
            telefone_raw="(11) 98765-4323",
            data_nascimento_input=date(1994, 2, 10)
        )

        # Barbeiro desliga a cadeira
        alternar_status_cadeira(self.barbeiro1, ficar_online=False)

        self.barbeiro1.refresh_from_db()
        self.assertFalse(self.barbeiro1.online)

        # p2 e p3 que estavam em espera foram excluídos
        self.assertFalse(ParticipacaoFila.objects.filter(pk=p2.pk).exists())
        self.assertFalse(ParticipacaoFila.objects.filter(pk=p3.pk).exists())

        # p1 em atendimento foi PRESERVADO!
        self.assertTrue(ParticipacaoFila.objects.filter(pk=p1.pk).exists())

        # Finalizar corte apaga p1 e a cadeira permanece offline sem chamar ninguém
        finalizar_corte(self.barbeiro1, p1.pk)
        self.assertFalse(ParticipacaoFila.objects.filter(pk=p1.pk).exists())
        self.assertEqual(ParticipacaoFila.objects.filter(barbeiro=self.barbeiro1).count(), 0)

    def test_desativar_barbeiro_rejeita_se_em_atendimento(self):
        p1, _, _ = entrar_na_fila(
            barbeiro_id=self.barbeiro1.id,
            primeiro_nome_raw="EmCorte",
            sobrenome_raw="Um",
            telefone_raw="(11) 98765-4321",
            data_nascimento_input=date(1998, 4, 15)
        )
        iniciar_atendimento(self.barbeiro1, p1.pk, str(p1.identificador_chamada))

        with self.assertRaises(ConflitoEstadoError):
            desativar_barbeiro(self.barbeiro1)

        # Finaliza o corte
        finalizar_corte(self.barbeiro1, p1.pk)

        # Agora consegue desativar
        desativar_barbeiro(self.barbeiro1)
        self.barbeiro1.refresh_from_db()
        self.assertFalse(self.barbeiro1.ativo)
        self.assertFalse(self.barbeiro1.online)
        self.assertFalse(self.barbeiro1.usuario.is_active)
