"""
Validadores e normalizadores para o sistema de fila da barbearia.
Guild Tech Support.
"""

import re
import unicodedata
from datetime import date
from django.core.exceptions import ValidationError
from django.utils import timezone


# Regex para validação de nomes Unicode (letras com acentos, apóstrofos e hífens permitidos)
# [^\W\d_] seleciona qualquer letra alfabética em qualquer alfabeto Unicode sem dígitos ou underscores.
NOME_REGEX = re.compile(r"^[^\W\d_]+(?:[ '-][^\W\d_]+)*$", re.UNICODE)

# DDDs válidos no Brasil (todos os DDDs regulamentados pela Anatel)
DDDS_VALIDOS = {
    11, 12, 13, 14, 15, 16, 17, 18, 19,  # SP
    21, 22, 24,                          # RJ
    27, 28,                              # ES
    31, 32, 33, 34, 35, 37, 38,          # MG
    41, 42, 43, 44, 45, 46,              # PR
    47, 48, 49,                          # SC
    51, 53, 54, 55,                      # RS
    61,                                  # DF
    62, 64,                              # GO
    63,                                  # TO
    65, 66,                              # MT
    67,                                  # MS
    68,                                  # AC
    69,                                  # RO
    71, 73, 74, 75, 77,                  # BA
    79,                                  # SE
    81, 87,                              # PE
    82,                                  # AL
    83,                                  # PB
    84,                                  # RN
    85, 88,                              # CE
    86, 89,                              # PI
    91, 93, 94,                          # PA
    92, 97,                              # AM
    95,                                  # RR
    96,                                  # AP
    98, 99,                              # MA
}


def normalizar_nome(valor: str) -> str:
    """
    Remove espaços das pontas e reduz múltiplos espaços internos para um espaço único.
    """
    if not valor:
        return ""
    return unicodedata.normalize("NFC", " ".join(valor.strip().split())).replace("’", "'")


def validar_primeiro_nome(valor: str) -> str:
    """
    Valida e normaliza o primeiro nome.
    Obrigatório, min 2 caracteres, max 50 caracteres, Unicode, acentos, apóstrofos e hífens.
    """
    normalizado = normalizar_nome(valor)
    if not normalizado:
        raise ValidationError("O primeiro nome é obrigatório.")
    if len(normalizado) < 2:
        raise ValidationError("O primeiro nome deve conter no mínimo 2 caracteres.")
    if len(normalizado) > 50:
        raise ValidationError("O primeiro nome deve conter no máximo 50 caracteres.")
    if not NOME_REGEX.match(normalizado):
        raise ValidationError(
            "O primeiro nome contém caracteres inválidos. Utilize apenas letras, acentos, hífens ou apóstrofos."
        )
    return normalizado


def validar_sobrenome(valor: str) -> str:
    """
    Valida e normaliza o sobrenome.
    Obrigatório, min 2 caracteres, max 100 caracteres, Unicode, acentos, apóstrofos e hífens.
    """
    normalizado = normalizar_nome(valor)
    if not normalizado:
        raise ValidationError("O sobrenome é obrigatório.")
    if len(normalizado) < 2:
        raise ValidationError("O sobrenome deve conter no mínimo 2 caracteres.")
    if len(normalizado) > 100:
        raise ValidationError("O sobrenome deve conter no máximo 100 caracteres.")
    if not NOME_REGEX.match(normalizado):
        raise ValidationError(
            "O sobrenome contém caracteres inválidos. Utilize apenas letras, acentos, hífens ou apóstrofos."
        )
    return normalizado


def normalizar_telefone(valor: str) -> str:
    """
    Extrai apenas os dígitos numéricos de um telefone brasileiro.
    Exemplo: "(11) 98765-4321" -> "11987654321"
    """
    if not valor:
        return ""
    return re.sub(r"\D", "", valor)


def validar_telefone(valor: str) -> str:
    """
    Valida e retorna o telefone brasileiro no formato canônico (apenas dígitos).
    Regras:
    - Exatamente 10 dígitos (fixo) ou 11 dígitos (celular);
    - DDD regulamentado e válido;
    - Celular (11 dígitos): nono dígito obrigatório (inicia com 9 após DDD);
    - Fixo (10 dígitos): inicia com 2 a 5 após DDD;
    - Rejeita sequências de dígitos repetidos (ex: 11111111111).
    """
    digitos = normalizar_telefone(valor)
    if not digitos:
        raise ValidationError("O telefone é obrigatório.")

    if len(digitos) not in (10, 11):
        raise ValidationError("O telefone deve conter DDD e número com 10 ou 11 dígitos.")

    # Rejeita todos os dígitos iguais
    if len(set(digitos)) == 1:
        raise ValidationError("Telefone inválido: sequência de números repetidos.")

    ddd = int(digitos[:2])
    if ddd not in DDDS_VALIDOS:
        raise ValidationError(f"DDD {ddd:02d} não é um código de área brasileiro válido.")

    primeiro_digito_num = digitos[2]
    if len(digitos) == 11:
        # Celular no Brasil deve ter 9 como primeiro dígito após DDD
        if primeiro_digito_num != '9':
            raise ValidationError("Celulares com 11 dígitos devem iniciar com o dígito 9 após o DDD.")
    else:
        # Fixo no Brasil inicia com dígitos 2, 3, 4 ou 5 após DDD
        if primeiro_digito_num not in ('2', '3', '4', '5'):
            raise ValidationError("Telefones fixos com 10 dígitos devem iniciar com 2, 3, 4 ou 5 após o DDD.")

    return digitos


def validar_data_nascimento(data_nasc: date) -> date:
    """
    Valida a data de nascimento:
    - Deve ser uma data no passado (anterior ao dia atual);
    - Não pode ser uma data impossível/futura;
    - Limite máximo defensivo de idade (130 anos).
    """
    if not data_nasc:
        raise ValidationError("A data de nascimento é obrigatória.")

    hoje = timezone.localdate()
    if data_nasc >= hoje:
        raise ValidationError("A data de nascimento deve ser no passado.")

    try:
        limite_passado = hoje.replace(year=hoje.year - 130)
    except ValueError:
        limite_passado = hoje.replace(year=hoje.year - 130, day=28)
    if data_nasc < limite_passado:
        raise ValidationError("Data de nascimento inválida.")

    return data_nasc
