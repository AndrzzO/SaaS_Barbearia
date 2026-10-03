"""
Camada de serviços de negócio do sistema de fila da barbearia.
Guild Tech Support.
Operações transacionais estritas com SQLite IMMEDIATE, tratamento de contenção e integridade.
"""

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import date, timedelta
from typing import Optional, Tuple, Dict, Any, List

from django.conf import settings
from django.db import transaction, OperationalError, IntegrityError
from django.db.models import Count, Q, Exists, OuterRef
from django.utils import timezone

from fila.models import Barbeiro, ParticipacaoFila, StatusParticipacao
from fila.validators import (
    validar_primeiro_nome,
    validar_sobrenome,
    validar_telefone,
    validar_data_nascimento,
)


class FilaError(Exception):
    """Exceção base de negócio do aplicativo de fila."""
    pass


class ValidacaoFilaError(FilaError):
    """Erro de validação de dados de entrada (HTTP 400)."""
    pass


class NaoAutorizadoError(FilaError):
    """Erro de permissão ou autenticação (HTTP 401/403)."""
    pass


class NaoEncontradoError(FilaError):
    """Recurso solicitado não foi encontrado (HTTP 404)."""
    pass


class ConflitoEstadoError(FilaError):
    """Transição de estado inválida ou cadeira em estado incompatível (HTTP 409)."""
    pass


class DuplicidadeTelefoneError(FilaError):
    """Telefone já cadastrado na fila ativa (HTTP 409)."""
    pass


class ContencaoBancoDadosError(FilaError):
    """Erro transitório de banco ocupado/bloqueado após retentativas esgotadas (HTTP 503)."""
    pass


def executar_com_retentativas_sqlite(funcao, max_tentativas: int = 5, backoff_base: float = 0.05):
    """
    Executa uma operação dentro de uma transação transacional imediata no SQLite.
    Se ocorrer 'database is locked', realiza rollback e retenta com backoff exponencial.
    """
    tentativa = 0
    while True:
        try:
            with transaction.atomic():
                return funcao()
        except IntegrityError as exc:
            raise ConflitoEstadoError("A operação conflita com o estado atual. Atualize a fila.") from exc
        except OperationalError as exc:
            msg = str(exc).lower()
            if "locked" in msg or "busy" in msg:
                tentativa += 1
                if tentativa >= max_tentativas:
                    raise ContencaoBancoDadosError(
                        "O banco de dados está temporariamente ocupado sob alta concorrência. Tente novamente."
                    ) from exc
                time.sleep(backoff_base * (2 ** (tentativa - 1)))
            else:
                raise


def calcular_hash_token(token_raw: str) -> str:
    """Calcula o hash SHA-256 do token de acesso do cliente."""
    return hashlib.sha256(token_raw.encode('utf-8')).hexdigest()


def gerar_token_e_hash() -> Tuple[str, str]:
    """Gera um token aleatório de alta entropia (32 bytes urlsafe) e seu hash SHA-256."""
    token_raw = secrets.token_urlsafe(32)
    token_hash = calcular_hash_token(token_raw)
    return token_raw, token_hash


def buscar_participacao_por_token(token_raw: Optional[str]) -> Optional[ParticipacaoFila]:
    """
    Localiza a participação ativa correspondente ao token temporário fornecido.
    Retorna None se o token for ausente, inválido ou pertencer a participação já excluída.
    """
    if not token_raw:
        return None
    token_hash = calcular_hash_token(token_raw)
    try:
        return ParticipacaoFila.objects.select_related('barbeiro').get(hash_token_acesso=token_hash)
    except ParticipacaoFila.DoesNotExist:
        return None


def listar_cadeiras_publicas() -> List[Dict[str, Any]]:
    """
    Retorna a lista de todas as cadeiras cadastradas com informações operacionais públicas.
    Sem N+1 e sem exibir qualquer dado de clientes.
    """
    atendimento_subquery = ParticipacaoFila.objects.filter(
        barbeiro=OuterRef('pk'),
        status=StatusParticipacao.EM_ATENDIMENTO
    )

    barbeiros = (
        Barbeiro.objects.all()
        .annotate(
            quantidade_aguardando=Count(
                'participacoes',
                filter=Q(participacoes__status=StatusParticipacao.AGUARDANDO)
            ),
            tem_atendimento=Exists(atendimento_subquery)
        )
        .order_by('nome_exibicao')
    )

    resultado = []
    for b in barbeiros:
        resultado.append({
            'identificador_barbeiro': b.id,
            'nome_exibicao': b.nome_exibicao,
            'ativo': b.ativo,
            'online': b.online,
            'aceita_entrada': b.aceita_entrada,
            'quantidade_aguardando': b.quantidade_aguardando,
            'atendimento_em_andamento': b.tem_atendimento,
        })
    return resultado


def entrar_na_fila(
    barbeiro_id: int,
    primeiro_nome_raw: str,
    sobrenome_raw: str,
    telefone_raw: str,
    data_nascimento_input: Any,
    token_cliente_atual: Optional[str] = None
) -> Tuple[ParticipacaoFila, str, bool]:
    """
    Cadastra uma nova participação temporária na fila de um barbeiro.
    Retorna (participacao, token_raw, ja_existia).
    Se o cliente já tiver credencial válida ativa, reapresenta seu acompanhamento.
    Se o telefone já estiver cadastrado sem credencial válida, rejeita genericamente.
    """
    # 1. Se cliente já tiver token válido para uma participação existente, reapresenta
    if token_cliente_atual:
        participacao_existente = buscar_participacao_por_token(token_cliente_atual)
        if participacao_existente:
            return participacao_existente, token_cliente_atual, True

    # 2. Validações preliminares de formato
    try:
        primeiro_nome = validar_primeiro_nome(primeiro_nome_raw)
        sobrenome = validar_sobrenome(sobrenome_raw)
        telefone = validar_telefone(telefone_raw)
        if isinstance(data_nascimento_input, str):
            data_nascimento = date.fromisoformat(data_nascimento_input)
        else:
            data_nascimento = data_nascimento_input
        data_nascimento = validar_data_nascimento(data_nascimento)
    except Exception as exc:
        raise ValidacaoFilaError(str(exc)) from exc

    def _operacao():
        # Revalida barbeiro dentro da transação imediata
        try:
            barbeiro = Barbeiro.objects.get(pk=barbeiro_id)
        except Barbeiro.DoesNotExist:
            raise NaoEncontradoError("Cadeira do barbeiro não encontrada.")

        if not barbeiro.ativo or not barbeiro.online:
            raise ConflitoEstadoError("Esta cadeira não está aceitando novas entradas na fila.")

        # Revalida duplicidade de telefone
        if ParticipacaoFila.objects.filter(telefone=telefone).exists():
            raise DuplicidadeTelefoneError(
                "Não foi possível concluir a entrada. Por favor, procure o barbeiro."
            )

        # Atribui ordem crescente
        ordem = barbeiro.proxima_ordem_fila
        barbeiro.proxima_ordem_fila += 1
        barbeiro.save(update_fields=['proxima_ordem_fila'])

        token_raw, token_hash = gerar_token_e_hash()

        participacao = ParticipacaoFila.objects.create(
            barbeiro=barbeiro,
            primeiro_nome=primeiro_nome,
            sobrenome=sobrenome,
            telefone=telefone,
            data_nascimento=data_nascimento,
            status=StatusParticipacao.AGUARDANDO,
            ordem_fila=ordem,
            hash_token_acesso=token_hash,
            aguardando_ultrapassagem=False,
            ultrapassagens_por_falta=0,
        )

        # Se cadeira livre e sem chamada, chama o próximo elegível
        cadeira_livre = not ParticipacaoFila.objects.filter(
            barbeiro=barbeiro,
            status=StatusParticipacao.EM_ATENDIMENTO
        ).exists()
        sem_chamada = not ParticipacaoFila.objects.filter(
            barbeiro=barbeiro,
            status=StatusParticipacao.CHAMADO
        ).exists()

        if cadeira_livre and sem_chamada:
            tentar_chamar_proximo_interno(barbeiro)
            participacao.refresh_from_db()

        return participacao, token_raw, False

    return executar_com_retentativas_sqlite(_operacao)


def tentar_chamar_proximo_interno(barbeiro: Barbeiro) -> Optional[ParticipacaoFila]:
    """
    Função interna (executada dentro de transação) para chamar o próximo cliente elegível.
    Elegível: status=AGUARDANDO e aguardando_ultrapassagem=False.
    """
    if not barbeiro.ativo or not barbeiro.online:
        return None

    # Se já há alguém em atendimento, não chama
    if ParticipacaoFila.objects.filter(barbeiro=barbeiro, status=StatusParticipacao.EM_ATENDIMENTO).exists():
        return None

    # Se já há alguém chamado, verifica se expirou
    chamado_atual = ParticipacaoFila.objects.filter(
        barbeiro=barbeiro,
        status=StatusParticipacao.CHAMADO
    ).first()

    agora = timezone.now()
    if chamado_atual:
        if chamado_atual.chamada_expira_em and agora >= chamado_atual.chamada_expira_em:
            processar_expiracao_interna(barbeiro, chamado_atual)
            return ParticipacaoFila.objects.filter(barbeiro=barbeiro, status=StatusParticipacao.CHAMADO).first()
        else:
            return chamado_atual

    # Busca o próximo elegível na ordem da fila
    proximo = ParticipacaoFila.objects.filter(
        barbeiro=barbeiro,
        status=StatusParticipacao.AGUARDANDO,
        aguardando_ultrapassagem=False
    ).order_by('ordem_fila', 'pk').first()

    if not proximo:
        return None

    agora = timezone.now()
    proximo.status = StatusParticipacao.CHAMADO
    proximo.chamada_iniciada_em = agora
    proximo.chamada_expira_em = agora + timedelta(seconds=30)
    proximo.identificador_chamada = uuid.uuid4()
    proximo.save(update_fields=[
        'status', 'chamada_iniciada_em', 'chamada_expira_em', 'identificador_chamada'
    ])
    return proximo


def processar_expiracao_interna(barbeiro: Barbeiro, participacao: ParticipacaoFila, chamar_seguinte=True) -> None:
    """
    Processa a expiração de uma chamada não confirmada (Passo 8):
    - Devolve para AGUARDANDO;
    - Marca aguardando_ultrapassagem=True;
    - Atribui nova ordem no final da fila;
    - Limpa campos de chamada;
    - NÃO incrementa contador de faltas aqui (Passo 9);
    - Tenta chamar o próximo elegível.
    """
    participacao.status = StatusParticipacao.AGUARDANDO
    participacao.aguardando_ultrapassagem = True
    participacao.chamada_iniciada_em = None
    participacao.chamada_expira_em = None
    participacao.identificador_chamada = None
    participacao.ordem_fila = barbeiro.proxima_ordem_fila
    barbeiro.proxima_ordem_fila += 1
    barbeiro.save(update_fields=['proxima_ordem_fila'])

    participacao.save(update_fields=[
        'status', 'aguardando_ultrapassagem', 'chamada_iniciada_em',
        'chamada_expira_em', 'identificador_chamada', 'ordem_fila'
    ])

    if chamar_seguinte:
        tentar_chamar_proximo_interno(barbeiro)


def processar_filas_servico() -> int:
    """
    Serviço invocado periodicamente pelo worker supervisor (Passo 13).
    Varre chamadas vencidas e cadeiras livres que precisam chamar o próximo elegível.
    Retorna a quantidade de operações processadas no ciclo.
    """
    operacoes = 0
    agora = timezone.now()

    # 1. Processa chamadas vencidas
    chamadas_vencidas_ids = list(
        ParticipacaoFila.objects.filter(
            status=StatusParticipacao.CHAMADO,
            chamada_expira_em__lte=agora
        ).values_list('pk', flat=True)
    )

    for p_id in chamadas_vencidas_ids:
        def _expirar():
            try:
                p = ParticipacaoFila.objects.select_related('barbeiro').get(pk=p_id)
            except ParticipacaoFila.DoesNotExist:
                return 0
            if p.status == StatusParticipacao.CHAMADO and p.chamada_expira_em and timezone.now() >= p.chamada_expira_em:
                processar_expiracao_interna(p.barbeiro, p)
                return 1
            return 0

        operacoes += executar_com_retentativas_sqlite(_expirar)

    # 2. Processa cadeiras online livres sem chamada e sem atendimento
    cadeiras_online = list(
        Barbeiro.objects.filter(ativo=True, online=True).values_list('pk', flat=True)
    )

    for b_id in cadeiras_online:
        def _chamar():
            try:
                barbeiro = Barbeiro.objects.get(pk=b_id)
            except Barbeiro.DoesNotExist:
                return 0
            if not barbeiro.ativo or not barbeiro.online:
                return 0
            livre = not ParticipacaoFila.objects.filter(
                barbeiro=barbeiro, status=StatusParticipacao.EM_ATENDIMENTO
            ).exists()
            sem_chamada = not ParticipacaoFila.objects.filter(
                barbeiro=barbeiro, status=StatusParticipacao.CHAMADO
            ).exists()
            if livre and sem_chamada:
                res = tentar_chamar_proximo_interno(barbeiro)
                return 1 if res else 0
            return 0

        operacoes += executar_com_retentativas_sqlite(_chamar)

    return operacoes


def iniciar_atendimento(barbeiro: Barbeiro, participacao_id: int, identificador_chamada_str: str) -> ParticipacaoFila:
    """
    Confirma a presença do cliente pelo barbeiro (Passo 8 e Passo 9):
    - Exige chamada vigente;
    - Exige identificador do ciclo atual;
    - Revalida prazo de 30 segundos;
    - Se expirou, persiste a expiração e reposicionamento no banco e levanta erro;
    - Altera para EM_ATENDIMENTO;
    - Registra iniciado_em;
    - Limpa campos da chamada;
    - Desativa aguardando_ultrapassagem dessa participação;
    - Regra das 5 Ultrapassagens (Passo 9):
      Para as OUTRAS participações dessa cadeira com aguardando_ultrapassagem=True:
      - incrementa 1 ultrapassagem;
      - se atingir 5: exclui imediatamente;
      - se < 5: torna aguardando_ultrapassagem=False (reabilita para nova chamada).
    """
    def _operacao():
        b = Barbeiro.objects.select_related('usuario').get(pk=barbeiro.pk)
        if not (b.ativo and b.online and b.usuario.is_active):
            raise ConflitoEstadoError("A cadeira está indisponível.")
        try:
            participacao = ParticipacaoFila.objects.get(pk=participacao_id, barbeiro=b)
        except ParticipacaoFila.DoesNotExist:
            raise NaoEncontradoError("Participação não encontrada nesta cadeira.")
        if participacao.status != StatusParticipacao.CHAMADO:
            raise ConflitoEstadoError("O participante não está sendo chamado.")
        agora = timezone.now()
        if agora >= participacao.chamada_expira_em:
            processar_expiracao_interna(b, participacao)
            return None
        if str(participacao.identificador_chamada) != str(identificador_chamada_str):
            raise ConflitoEstadoError("Identificador do ciclo de chamada não corresponde à chamada atual.")
        if ParticipacaoFila.objects.filter(barbeiro=b, status=StatusParticipacao.EM_ATENDIMENTO).exists():
            raise ConflitoEstadoError("Já existe um atendimento nesta cadeira.")
        participacao.status = StatusParticipacao.EM_ATENDIMENTO
        participacao.iniciado_em = agora
        participacao.chamada_iniciada_em = None
        participacao.chamada_expira_em = None
        participacao.identificador_chamada = None
        participacao.aguardando_ultrapassagem = False
        participacao.save(update_fields=['status', 'iniciado_em', 'chamada_iniciada_em',
            'chamada_expira_em', 'identificador_chamada', 'aguardando_ultrapassagem'])
        for outro in ParticipacaoFila.objects.filter(barbeiro=b, aguardando_ultrapassagem=True).exclude(pk=participacao.pk):
            outro.ultrapassagens_por_falta += 1
            if outro.ultrapassagens_por_falta >= 5:
                outro.delete()
            else:
                outro.aguardando_ultrapassagem = False
                outro.save(update_fields=['ultrapassagens_por_falta', 'aguardando_ultrapassagem'])
        return participacao
    resultado = executar_com_retentativas_sqlite(_operacao)
    if resultado is None:
        raise ConflitoEstadoError("A chamada expirou e a participação foi reposicionada na fila.")
    return resultado


def chamar_proximo_manual(barbeiro: Barbeiro) -> ParticipacaoFila:
    """Ação manual do barbeiro para chamar o próximo elegível na ordem."""
    def _operacao():
        barbeiro.refresh_from_db()
        if not barbeiro.ativo or not barbeiro.online:
            raise ConflitoEstadoError("A cadeira está inativa ou offline.")

        if ParticipacaoFila.objects.filter(barbeiro=barbeiro, status=StatusParticipacao.EM_ATENDIMENTO).exists():
            raise ConflitoEstadoError("Não é permitido chamar enquanto houver atendimento em andamento.")

        chamado_atual = ParticipacaoFila.objects.filter(
            barbeiro=barbeiro, status=StatusParticipacao.CHAMADO
        ).first()

        agora = timezone.now()
        if chamado_atual:
            if chamado_atual.chamada_expira_em and agora >= chamado_atual.chamada_expira_em:
                processar_expiracao_interna(barbeiro, chamado_atual)
            else:
                raise ConflitoEstadoError("Já existe uma chamada em andamento para esta cadeira.")

        proximo = tentar_chamar_proximo_interno(barbeiro)
        return proximo

    resultado = executar_com_retentativas_sqlite(_operacao)
    if resultado is None:
        raise ConflitoEstadoError("Não há participantes elegíveis aguardando na fila.")
    return resultado


def chamar_especifico(barbeiro: Barbeiro, participacao_id: int) -> ParticipacaoFila:
    """
    Ação manual do barbeiro para chamar um cliente específico que esteja AGUARDANDO.
    Se já houver outro CHAMADO não-expirado, devolve-o à espera sem penalidade nem perda de ordem.
    """
    def _operacao():
        barbeiro.refresh_from_db()
        if not barbeiro.ativo or not barbeiro.online:
            raise ConflitoEstadoError("A cadeira está inativa ou offline.")

        if ParticipacaoFila.objects.filter(barbeiro=barbeiro, status=StatusParticipacao.EM_ATENDIMENTO).exists():
            raise ConflitoEstadoError("Não é permitido chamar enquanto houver atendimento em andamento.")

        try:
            escolhido = ParticipacaoFila.objects.get(pk=participacao_id, barbeiro=barbeiro)
        except ParticipacaoFila.DoesNotExist:
            raise NaoEncontradoError("Participação não encontrada nesta cadeira.")

        if escolhido.status != StatusParticipacao.AGUARDANDO:
            raise ConflitoEstadoError("O participante escolhido deve estar no estado AGUARDANDO.")

        chamado_atual = ParticipacaoFila.objects.filter(
            barbeiro=barbeiro, status=StatusParticipacao.CHAMADO
        ).first()

        agora = timezone.now()
        if chamado_atual:
            if chamado_atual.chamada_expira_em and agora >= chamado_atual.chamada_expira_em:
                processar_expiracao_interna(barbeiro, chamado_atual, chamar_seguinte=False)
            else:
                # Devolve o chamado anterior à espera sem penalidade e sem perder sua ordem
                chamado_atual.status = StatusParticipacao.AGUARDANDO
                chamado_atual.chamada_iniciada_em = None
                chamado_atual.chamada_expira_em = None
                chamado_atual.identificador_chamada = None
                chamado_atual.save(update_fields=[
                    'status', 'chamada_iniciada_em', 'chamada_expira_em', 'identificador_chamada'
                ])

        # Chama a pessoa escolhida (inclusive se estava aguardando_ultrapassagem)
        escolhido.status = StatusParticipacao.CHAMADO
        escolhido.chamada_iniciada_em = agora
        escolhido.chamada_expira_em = agora + timedelta(seconds=30)
        escolhido.identificador_chamada = uuid.uuid4()
        escolhido.save(update_fields=[
            'status', 'chamada_iniciada_em', 'chamada_expira_em', 'identificador_chamada'
        ])
        return escolhido

    return executar_com_retentativas_sqlite(_operacao)


def finalizar_corte(barbeiro: Barbeiro, participacao_id: int) -> None:
    """
    Finaliza o corte em andamento (Passo 11):
    - Exige EM_ATENDIMENTO;
    - Exclui a participação e seus dados;
    - Libera a cadeira;
    - Se continuar online e ativo, chama o próximo elegível.
    """
    def _operacao():
        barbeiro.refresh_from_db()
        try:
            participacao = ParticipacaoFila.objects.get(pk=participacao_id, barbeiro=barbeiro)
        except ParticipacaoFila.DoesNotExist:
            raise NaoEncontradoError("Atendimento não encontrado nesta cadeira.")

        if participacao.status != StatusParticipacao.EM_ATENDIMENTO:
            raise ConflitoEstadoError("Apenas atendimentos em andamento podem ser finalizados.")

        participacao.delete()

        if barbeiro.ativo and barbeiro.online:
            tentar_chamar_proximo_interno(barbeiro)

    return executar_com_retentativas_sqlite(_operacao)


def sair_da_fila(participacao: ParticipacaoFila) -> None:
    """
    O próprio cliente solicita saída da fila usando seu token (Passo 11):
    - Permitido somente em AGUARDANDO ou CHAMADO;
    - Rejeita se EM_ATENDIMENTO;
    - Exclui a participação e dados;
    - Se era CHAMADO, tenta chamar o próximo elegível.
    """
    def _operacao():
        try:
            p = ParticipacaoFila.objects.select_related('barbeiro').get(pk=participacao.pk)
        except ParticipacaoFila.DoesNotExist:
            raise NaoEncontradoError("Participação encerrada.")
        if p.status == StatusParticipacao.EM_ATENDIMENTO:
            raise ConflitoEstadoError("Não é permitido sair da fila durante o atendimento.")

        era_chamado = (p.status == StatusParticipacao.CHAMADO)
        barbeiro = p.barbeiro
        p.delete()

        if era_chamado and barbeiro.ativo and barbeiro.online:
            tentar_chamar_proximo_interno(barbeiro)

    return executar_com_retentativas_sqlite(_operacao)


def remover_participante(usuario, participacao_id: int) -> None:
    """
    Remoção administrativa da fila pelo barbeiro responsável ou pelo proprietário (Passo 11):
    - Exige barbeiro proprietário da fila ou superusuário ativo;
    - Permitido somente em AGUARDANDO ou CHAMADO;
    - Rejeita se EM_ATENDIMENTO;
    - Exclui a participação;
    - Se era CHAMADO, busca o próximo elegível.
    """
    def _operacao():
        try:
            p = ParticipacaoFila.objects.select_related('barbeiro', 'barbeiro__usuario').get(pk=participacao_id)
        except ParticipacaoFila.DoesNotExist:
            raise NaoEncontradoError("Participação não encontrada.")

        # Validação de autorização
        is_proprietario = usuario.is_superuser and usuario.is_active
        is_barbeiro_da_cadeira = (p.barbeiro.usuario_id == usuario.id) and usuario.is_active

        if not (is_proprietario or is_barbeiro_da_cadeira):
            raise NaoAutorizadoError("Você não possui permissão para remover participantes desta cadeira.")

        if p.status == StatusParticipacao.EM_ATENDIMENTO:
            raise ConflitoEstadoError("Não é permitido remover cliente em atendimento. Finalize o corte.")

        era_chamado = (p.status == StatusParticipacao.CHAMADO)
        barbeiro = p.barbeiro
        p.delete()

        if era_chamado and barbeiro.ativo and barbeiro.online:
            tentar_chamar_proximo_interno(barbeiro)

    return executar_com_retentativas_sqlite(_operacao)


def alternar_status_cadeira(barbeiro: Barbeiro, ficar_online: bool) -> Barbeiro:
    """
    Liga ou desliga a cadeira do barbeiro (Passo 12):
    Ao desligar:
    - Impede novas entradas;
    - Exclui AGUARDANDO e CHAMADO;
    - Invalida credenciais ao excluir os registros;
    - Encerra chamadas e não faz novas chamadas;
    - Se houver EM_ATENDIMENTO, preserva somente ele até sua finalização.
    Ao ligar:
    - Requer barbeiro ativo;
    - Se livre e sem chamada, chama o próximo (se houver alguém).
    """
    def _operacao():
        b = Barbeiro.objects.get(pk=barbeiro.pk)
        if ficar_online:
            if not b.ativo:
                raise ConflitoEstadoError("Barbeiro desativado não pode ficar online.")
            b.online = True
            b.save(update_fields=['online'])

            livre = not ParticipacaoFila.objects.filter(
                barbeiro=b, status=StatusParticipacao.EM_ATENDIMENTO
            ).exists()
            sem_chamada = not ParticipacaoFila.objects.filter(
                barbeiro=b, status=StatusParticipacao.CHAMADO
            ).exists()
            if livre and sem_chamada:
                tentar_chamar_proximo_interno(b)
        else:
            b.online = False
            b.save(update_fields=['online'])
            # Exclui AGUARDANDO e CHAMADO atomicamente
            ParticipacaoFila.objects.filter(
                barbeiro=b,
                status__in=[StatusParticipacao.AGUARDANDO, StatusParticipacao.CHAMADO]
            ).delete()

        return b

    return executar_com_retentativas_sqlite(_operacao)


def desativar_barbeiro(barbeiro: Barbeiro) -> None:
    """
    Desativa um barbeiro pelo proprietário (Passo 16):
    - Rejeita se houver EM_ATENDIMENTO;
    - Limpa a fila aguardando e chamada;
    - Coloca offline;
    - Desativa a conta do usuário (is_active=False);
    - Desativa o barbeiro (ativo=False).
    """
    def _operacao():
        b = Barbeiro.objects.select_related('usuario').get(pk=barbeiro.pk)
        if ParticipacaoFila.objects.filter(barbeiro=b, status=StatusParticipacao.EM_ATENDIMENTO).exists():
            raise ConflitoEstadoError(
                "Não é possível desativar o barbeiro enquanto houver atendimento em andamento. Finalize o corte antes."
            )

        b.participacoes.all().delete()
        b.online = False
        b.ativo = False
        b.save(update_fields=['online', 'ativo'])

        b.usuario.is_active = False
        b.usuario.save(update_fields=['is_active'])

    return executar_com_retentativas_sqlite(_operacao)
