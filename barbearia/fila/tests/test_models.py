"""
Testes de modelos, propriedades calculadas, validadores e restrições de integridade do banco de dados.
Guild Tech Support.
"""

import uuid
from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from fila.models import Barbeiro, ParticipacaoFila, StatusParticipacao, RegistroTentativaLoginIP
from fila.validators import (
    validar_primeiro_nome,
    validar_sobrenome,
    validar_telefone,
    validar_data_nascimento,
    normalizar_nome,
)

User = get_user_model()


class TestValidadores(TestCase):
    """Testa os validadores e normalizadores de dados."""

    def test_primeiro_nome_valido(self):
        self.assertEqual(validar_primeiro_nome("  João  "), "João")
        self.assertEqual(validar_primeiro_nome("Renée"), "Renée")
        self.assertEqual(validar_primeiro_nome("Jean-Luc"), "Jean-Luc")
        self.assertEqual(validar_primeiro_nome("D'Angelo"), "D'Angelo")
        self.assertEqual(validar_primeiro_nome("Müller"), "Müller")

    def test_primeiro_nome_invalido(self):
        with self.assertRaises(ValidationError):
            validar_primeiro_nome("")
        with self.assertRaises(ValidationError):
            validar_primeiro_nome("   ")
        with self.assertRaises(ValidationError):
            validar_primeiro_nome("A")  # menor que 2
        with self.assertRaises(ValidationError):
            validar_primeiro_nome("João123")  # contém número
        with self.assertRaises(ValidationError):
            validar_primeiro_nome("João @ Silva")  # caractere proibido

    def test_sobrenome_valido(self):
        self.assertEqual(validar_sobrenome("  da Silva  "), "da Silva")
        self.assertEqual(validar_sobrenome("D'Ávila"), "D'Ávila")
        self.assertEqual(validar_sobrenome("Saint-Germain"), "Saint-Germain")

    def test_sobrenome_invalido(self):
        with self.assertRaises(ValidationError):
            validar_sobrenome("")
        with self.assertRaises(ValidationError):
            validar_sobrenome("   ")
        with self.assertRaises(ValidationError):
            validar_sobrenome("S")
        with self.assertRaises(ValidationError):
            validar_sobrenome("Silva 2")

    def test_validar_telefone(self):
        # Celular válido (11 dígitos, DDD 11, nono dígito 9)
        self.assertEqual(validar_telefone("(11) 98765-4321"), "11987654321")
        self.assertEqual(validar_telefone("21 99999 8888"), "21999998888")

        # Fixo válido (10 dígitos, DDD 11, inicia com 2 a 5)
        self.assertEqual(validar_telefone("(11) 3456-7890"), "1134567890")
        self.assertEqual(validar_telefone("41 2100 0000"), "4121000000")

        # Inválidos
        with self.assertRaises(ValidationError):
            validar_telefone("")
        with self.assertRaises(ValidationError):
            validar_telefone("123")  # tamanho insuficiente
        with self.assertRaises(ValidationError):
            validar_telefone("119999999999")  # 12 dígitos
        with self.assertRaises(ValidationError):
            validar_telefone("01987654321")  # DDD 01 não existe
        with self.assertRaises(ValidationError):
            validar_telefone("11887654321")  # Celular não iniciando com 9
        with self.assertRaises(ValidationError):
            validar_telefone("11111111111")  # Todos iguais
        with self.assertRaises(ValidationError):
            validar_telefone("1112345678")  # Fixo iniciando com 1

    def test_validar_data_nascimento(self):
        hoje = timezone.localdate()
        ontem = hoje - timedelta(days=1)
        self.assertEqual(validar_data_nascimento(ontem), ontem)

        # Rejeita hoje e datas futuras
        with self.assertRaises(ValidationError):
            validar_data_nascimento(hoje)
        with self.assertRaises(ValidationError):
            validar_data_nascimento(hoje + timedelta(days=1))

        # Rejeita data impossível no passado remoto
        com_131_anos = hoje.replace(year=hoje.year - 131)
        with self.assertRaises(ValidationError):
            validar_data_nascimento(com_131_anos)


class TestModelosERestricoes(TestCase):
    """Testa os modelos, restrições e cálculos dinâmicos."""

    def setUp(self):
        self.usuario_barbeiro = User.objects.create_user(
            username="carlos",
            password="senha_segura_123",
            first_name="Carlos",
            last_name="Barbeiro"
        )
        self.barbeiro = Barbeiro.objects.create(
            usuario=self.usuario_barbeiro,
            nome_exibicao="Carlos Barbeiro",
            ativo=True,
            online=True,
            proxima_ordem_fila=1
        )

    def test_criacao_barbeiro(self):
        self.assertTrue(self.barbeiro.aceita_entrada)
        self.assertEqual(str(self.barbeiro), "Carlos Barbeiro (Online)")

        self.barbeiro.online = False
        self.barbeiro.save()
        self.assertFalse(self.barbeiro.aceita_entrada)
        self.assertEqual(str(self.barbeiro), "Carlos Barbeiro (Offline)")

    def test_calculo_idade_e_nome_completo(self):
        hoje = timezone.localdate()
        # Se hoje é dia 03/10/2026:
        # Aniversário ontem (já fez aniversário este ano)
        ontem_ano_passado = date(hoje.year - 25, hoje.month, hoje.day) - timedelta(days=1)
        participacao1 = ParticipacaoFila.objects.create(
            barbeiro=self.barbeiro,
            primeiro_nome="Lucas",
            sobrenome="Almeida",
            telefone="11987654321",
            data_nascimento=ontem_ano_passado,
            status=StatusParticipacao.AGUARDANDO,
            ordem_fila=1,
            hash_token_acesso="hash1"
        )
        self.assertEqual(participacao1.nome_completo, "Lucas Almeida")
        self.assertEqual(participacao1.idade, 25)

        # Aniversário amanhã (ainda não fez aniversário este ano)
        amanha_ano_passado = date(hoje.year - 25, hoje.month, hoje.day) + timedelta(days=1)
        participacao2 = ParticipacaoFila.objects.create(
            barbeiro=self.barbeiro,
            primeiro_nome="Marcos",
            sobrenome="Souza",
            telefone="11987654322",
            data_nascimento=amanha_ano_passado,
            status=StatusParticipacao.AGUARDANDO,
            ordem_fila=2,
            hash_token_acesso="hash2"
        )
        self.assertEqual(participacao2.idade, 24)

    def test_protecao_exclusao_barbeiro_com_participacao(self):
        ParticipacaoFila.objects.create(
            barbeiro=self.barbeiro,
            primeiro_nome="Lucas",
            sobrenome="Almeida",
            telefone="11987654321",
            data_nascimento=date(1995, 5, 10),
            status=StatusParticipacao.AGUARDANDO,
            ordem_fila=1,
            hash_token_acesso="hash1"
        )
        # Exclusão do barbeiro deve ser impedida por ProtectedError
        with self.assertRaises(ProtectedError):
            self.barbeiro.delete()

    def test_constraint_telefone_unico(self):
        ParticipacaoFila.objects.create(
            barbeiro=self.barbeiro,
            primeiro_nome="Lucas",
            sobrenome="Almeida",
            telefone="11987654321",
            data_nascimento=date(1995, 5, 10),
            status=StatusParticipacao.AGUARDANDO,
            ordem_fila=1,
            hash_token_acesso="hash1"
        )
        # Tentar criar outra participação com mesmo telefone
        from django.db import transaction
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Outro",
                    sobrenome="Cliente",
                    telefone="11987654321",
                    data_nascimento=date(1990, 1, 1),
                    status=StatusParticipacao.AGUARDANDO,
                    ordem_fila=2,
                    hash_token_acesso="hash2"
                )

    def test_constraint_ordem_fila_unica_por_barbeiro(self):
        ParticipacaoFila.objects.create(
            barbeiro=self.barbeiro,
            primeiro_nome="Lucas",
            sobrenome="Almeida",
            telefone="11987654321",
            data_nascimento=date(1995, 5, 10),
            status=StatusParticipacao.AGUARDANDO,
            ordem_fila=1,
            hash_token_acesso="hash1"
        )
        # Mesma ordem para mesmo barbeiro deve violar constraint
        from django.db import transaction
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Marcos",
                    sobrenome="Souza",
                    telefone="11987654322",
                    data_nascimento=date(1990, 1, 1),
                    status=StatusParticipacao.AGUARDANDO,
                    ordem_fila=1,
                    hash_token_acesso="hash2"
                )

    def test_constraint_somente_um_chamado_por_barbeiro(self):
        agora = timezone.now()
        expira = agora + timedelta(seconds=30)
        ParticipacaoFila.objects.create(
            barbeiro=self.barbeiro,
            primeiro_nome="Lucas",
            sobrenome="Almeida",
            telefone="11987654321",
            data_nascimento=date(1995, 5, 10),
            status=StatusParticipacao.CHAMADO,
            ordem_fila=1,
            chamada_iniciada_em=agora,
            chamada_expira_em=expira,
            identificador_chamada=uuid.uuid4(),
            hash_token_acesso="hash1"
        )
        # Segundo CHAMADO no mesmo barbeiro deve violar constraint
        from django.db import transaction
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Marcos",
                    sobrenome="Souza",
                    telefone="11987654322",
                    data_nascimento=date(1990, 1, 1),
                    status=StatusParticipacao.CHAMADO,
                    ordem_fila=2,
                    chamada_iniciada_em=agora,
                    chamada_expira_em=expira,
                    identificador_chamada=uuid.uuid4(),
                    hash_token_acesso="hash2"
                )

    def test_constraint_somente_um_em_atendimento_por_barbeiro(self):
        agora = timezone.now()
        entrada = agora - timedelta(minutes=5)
        ParticipacaoFila.objects.create(
            barbeiro=self.barbeiro,
            primeiro_nome="Lucas",
            sobrenome="Almeida",
            telefone="11987654321",
            data_nascimento=date(1995, 5, 10),
            status=StatusParticipacao.EM_ATENDIMENTO,
            ordem_fila=1,
            entrou_na_fila_em=entrada,
            iniciado_em=agora,
            hash_token_acesso="hash1"
        )
        # Segundo EM_ATENDIMENTO no mesmo barbeiro deve violar constraint
        from django.db import transaction
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Marcos",
                    sobrenome="Souza",
                    telefone="11987654322",
                    data_nascimento=date(1990, 1, 1),
                    status=StatusParticipacao.EM_ATENDIMENTO,
                    ordem_fila=2,
                    entrou_na_fila_em=entrada,
                    iniciado_em=agora,
                    hash_token_acesso="hash2"
                )

    def test_constraint_campos_chamada_apenas_em_chamado(self):
        agora = timezone.now()
        expira = agora + timedelta(seconds=30)
        from django.db import transaction
        # Tentar salvar AGUARDANDO com campos de chamada preenchidos
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Lucas",
                    sobrenome="Almeida",
                    telefone="11987654321",
                    data_nascimento=date(1995, 5, 10),
                    status=StatusParticipacao.AGUARDANDO,
                    ordem_fila=1,
                    chamada_iniciada_em=agora,
                    chamada_expira_em=expira,
                    identificador_chamada=uuid.uuid4(),
                    hash_token_acesso="hash1"
                )

        # Tentar salvar CHAMADO sem os campos de chamada
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Lucas",
                    sobrenome="Almeida",
                    telefone="11987654321",
                    data_nascimento=date(1995, 5, 10),
                    status=StatusParticipacao.CHAMADO,
                    ordem_fila=1,
                    hash_token_acesso="hash1"
                )

    def test_constraint_iniciado_em_apenas_em_atendimento(self):
        agora = timezone.now()
        from django.db import transaction
        # AGUARDANDO com iniciado_em preenchido deve falhar
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Lucas",
                    sobrenome="Almeida",
                    telefone="11987654321",
                    data_nascimento=date(1995, 5, 10),
                    status=StatusParticipacao.AGUARDANDO,
                    ordem_fila=1,
                    iniciado_em=agora,
                    hash_token_acesso="hash1"
                )

        # EM_ATENDIMENTO sem iniciado_em deve falhar
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Lucas",
                    sobrenome="Almeida",
                    telefone="11987654321",
                    data_nascimento=date(1995, 5, 10),
                    status=StatusParticipacao.EM_ATENDIMENTO,
                    ordem_fila=1,
                    hash_token_acesso="hash1"
                )

    def test_constraint_chamada_expira_apos_inicio(self):
        agora = timezone.now()
        from django.db import transaction
        # Expira antes ou igual ao início deve falhar
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Lucas",
                    sobrenome="Almeida",
                    telefone="11987654321",
                    data_nascimento=date(1995, 5, 10),
                    status=StatusParticipacao.CHAMADO,
                    ordem_fila=1,
                    chamada_iniciada_em=agora,
                    chamada_expira_em=agora - timedelta(seconds=1),
                    identificador_chamada=uuid.uuid4(),
                    hash_token_acesso="hash1"
                )

    def test_constraint_iniciado_em_nao_anterior_entrada(self):
        agora = timezone.now()
        entrada = agora + timedelta(minutes=5)
        from django.db import transaction
        # Iniciado anterior à entrada na fila deve falhar
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Lucas",
                    sobrenome="Almeida",
                    telefone="11987654321",
                    data_nascimento=date(1995, 5, 10),
                    status=StatusParticipacao.EM_ATENDIMENTO,
                    ordem_fila=1,
                    entrou_na_fila_em=entrada,
                    iniciado_em=agora,
                    hash_token_acesso="hash1"
                )

    def test_constraint_ultrapassagens_faixa(self):
        from django.db import transaction
        # Ultrapassagens acima de 5 deve falhar
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ParticipacaoFila.objects.create(
                    barbeiro=self.barbeiro,
                    primeiro_nome="Lucas",
                    sobrenome="Almeida",
                    telefone="11987654321",
                    data_nascimento=date(1995, 5, 10),
                    status=StatusParticipacao.AGUARDANDO,
                    ordem_fila=1,
                    ultrapassagens_por_falta=6,
                    hash_token_acesso="hash1"
                )


