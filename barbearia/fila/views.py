"""
Endpoints e controladores da API do sistema de fila da barbearia.
Guild Tech Support.
Respostas padronizadas em JSON com códigos de status HTTP semânticos e cabeçalhos de segurança.
"""

import json
import math
from datetime import datetime
from functools import wraps
from typing import Any, Dict

from django.conf import settings
from django.db import IntegrityError, OperationalError
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from fila.services import executar_com_retentativas_sqlite
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from fila.models import Barbeiro, ParticipacaoFila, StatusParticipacao
from fila.rate_limit import (
    obter_ip_cliente,
    registrar_e_validar_tentativa_login,
    RateLimitExcedido,
)
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
    buscar_participacao_por_token,
    ValidacaoFilaError,
    DuplicidadeTelefoneError,
    ConflitoEstadoError,
    NaoAutorizadoError,
    NaoEncontradoError,
    ContencaoBancoDadosError,
)

User = get_user_model()


def resposta_json_no_store(data: Any, status: int = 200) -> JsonResponse:
    """Retorna JsonResponse com cabeçalho de segurança Cache-Control: no-store."""
    resp = JsonResponse(data, status=status, safe=isinstance(data, dict))
    resp['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp['Pragma'] = 'no-cache'
    return resp


def extrair_json(request: HttpRequest) -> Dict[str, Any]:
    """Extrai e decodifica o corpo JSON da requisição com tratamento de erro."""
    if not request.body:
        return {}
    try:
        dados = json.loads(request.body.decode('utf-8'))
        if not isinstance(dados, dict):
            raise ValidacaoFilaError("Envie um objeto JSON.")
        for chave, valor in dados.items():
            if chave in ('online', 'ativo'):
                if not isinstance(valor, bool):
                    raise ValidacaoFilaError("O estado deve ser um booleano.")
            elif not isinstance(valor, str):
                raise ValidacaoFilaError("Os campos devem conter texto.")
        return dados
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidacaoFilaError("Formato de JSON inválido no corpo da requisição.")


def tratar_excecoes_fila(view_func):
    """Decorator para captura unificada e tradução semântica de exceções da aplicação."""
    @wraps(view_func)
    def wrapper(request: HttpRequest, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except ValidationError as exc:
            return resposta_json_no_store({'erro': " ".join(exc.messages)}, status=400)
        except IntegrityError:
            return resposta_json_no_store({'erro': "Conflito com os dados atuais."}, status=409)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'busy' not in str(exc).lower():
                raise
            return resposta_json_no_store({'erro': "Banco ocupado. Tente novamente."}, status=503)
        except ValidacaoFilaError as exc:
            return resposta_json_no_store({'erro': str(exc)}, status=400)
        except NaoAutorizadoError as exc:
            return resposta_json_no_store({'erro': str(exc)}, status=403)
        except NaoEncontradoError as exc:
            return resposta_json_no_store({'erro': str(exc)}, status=404)
        except DuplicidadeTelefoneError as exc:
            return resposta_json_no_store({'erro': str(exc)}, status=409)
        except ConflitoEstadoError as exc:
            return resposta_json_no_store({'erro': str(exc)}, status=409)
        except ContencaoBancoDadosError as exc:
            return resposta_json_no_store({'erro': str(exc)}, status=503)
        except RateLimitExcedido as exc:
            resp = resposta_json_no_store({'erro': str(exc), 'retry_after': exc.segundos_restantes}, status=429)
            resp['Retry-After'] = str(exc.segundos_restantes)
            return resp
    return wrapper


def requer_barbeiro_autenticado(view_func):
    """Garante que a requisição é de um barbeiro ativo devidamente autenticado."""
    @wraps(view_func)
    def wrapper(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return resposta_json_no_store({'erro': "Autenticação obrigatória."}, status=401)
        if not request.user.is_active:
            return resposta_json_no_store({'erro': "Conta de usuário inativa."}, status=403)
        if not hasattr(request.user, 'barbeiro'):
            return resposta_json_no_store({'erro': "Usuário não vinculado a uma cadeira de barbeiro."}, status=403)
        barbeiro = request.user.barbeiro
        if not barbeiro.ativo:
            return resposta_json_no_store({'erro': "Cadeira desativada pelo proprietário."}, status=403)
        return view_func(request, barbeiro, *args, **kwargs)
    return wrapper


def requer_proprietario_autenticado(view_func):
    """Garante que a requisição é do proprietário (superusuário ativo)."""
    @wraps(view_func)
    def wrapper(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return resposta_json_no_store({'erro': "Autenticação obrigatória."}, status=401)
        if not (request.user.is_active and request.user.is_superuser):
            return resposta_json_no_store({'erro': "Acesso restrito ao proprietário."}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


# ==============================================================================
# 1. ENDPOINTS PÚBLICOS
# ==============================================================================

@require_GET
@ensure_csrf_cookie
def home_cadeiras_publicas(request: HttpRequest) -> JsonResponse:
    """
    Retorna a listagem de todas as cadeiras cadastradas com disponibilidade operacional.
    Não revela dados de clientes nem listas nominais.
    """
    cadeiras = listar_cadeiras_publicas()
    return JsonResponse({
        'cadeiras': cadeiras,
        'horario_servidor': timezone.now().isoformat(),
    })


@require_POST
@tratar_excecoes_fila
def cliente_entrar_na_fila(request: HttpRequest, barbeiro_id: int) -> HttpResponse:
    """
    Cadastra uma nova participação temporária na fila da cadeira escolhida.
    Entrega o token secreto de acesso via cookie HttpOnly.
    """
    dados = extrair_json(request)
    token_atual = request.COOKIES.get(settings.FILA_COOKIE_NOME)

    participacao, token_raw, ja_existia = entrar_na_fila(
        barbeiro_id=barbeiro_id,
        primeiro_nome_raw=dados.get('primeiro_nome', ''),
        sobrenome_raw=dados.get('sobrenome', ''),
        telefone_raw=dados.get('telefone', ''),
        data_nascimento_input=dados.get('data_nascimento', ''),
        token_cliente_atual=token_atual
    )

    status_code = 200 if ja_existia else 201
    mensagem = "Acompanhamento reapresentado." if ja_existia else "Participação registrada com sucesso."

    resp = resposta_json_no_store({
        'mensagem': mensagem,
        'identificador_publico': str(participacao.identificador_publico),
        'ordem_fila': participacao.ordem_fila,
        'status': participacao.status,
    }, status=status_code)

    # Configuração segura do cookie de acesso temporário
    resp.set_cookie(
        key=settings.FILA_COOKIE_NOME,
        value=token_raw,
        httponly=settings.FILA_COOKIE_HTTPONLY,
        secure=settings.FILA_COOKIE_SECURE,
        samesite=settings.FILA_COOKIE_SAMESITE,
        path='/'
    )
    return resp


# ==============================================================================
# 2. ENDPOINTS DO CLIENTE TEMPORÁRIO
# ==============================================================================

@require_GET
def cliente_acompanhamento(request: HttpRequest) -> JsonResponse:
    """
    Acompanhamento individual da participação do cliente (Passo 14).
    Retorna o contrato completo para alerta sonoro no frontend.
    Se a participação já foi encerrada ou o cookie for inválido, limpa o cookie.
    """
    token_cookie = request.COOKIES.get(settings.FILA_COOKIE_NOME)
    participacao = buscar_participacao_por_token(token_cookie)

    if not participacao:
        resp = resposta_json_no_store({
            'status': 'ENCERRADO',
            'mensagem': 'Participação encerrada.',
            'horario_servidor': timezone.now().isoformat(),
        }, status=200)
        resp.delete_cookie(settings.FILA_COOKIE_NOME, path='/')
        return resp

    agora = timezone.now()
    tempo_restante_segundos = 0
    if participacao.status == StatusParticipacao.CHAMADO and participacao.chamada_expira_em:
        if participacao.chamada_expira_em > agora:
            tempo_restante_segundos = max(0, math.ceil((participacao.chamada_expira_em - agora).total_seconds()))

    # Quantidade de clientes à frente aguardando na mesma cadeira
    clientes_na_frente = ParticipacaoFila.objects.filter(
        barbeiro=participacao.barbeiro,
        status__in=[StatusParticipacao.AGUARDANDO, StatusParticipacao.CHAMADO],
        ordem_fila__lt=participacao.ordem_fila
    ).count()

    dados_acompanhamento = {
        'status': participacao.status,
        'identificador_publico': str(participacao.identificador_publico),
        'ordem_fila': participacao.ordem_fila,
        'clientes_na_frente': clientes_na_frente if participacao.status == StatusParticipacao.AGUARDANDO else 0,
        'entrou_na_fila_em': participacao.entrou_na_fila_em.isoformat(),
        'aguardando_ultrapassagem': participacao.aguardando_ultrapassagem,
        'identificador_chamada': str(participacao.identificador_chamada) if participacao.identificador_chamada else None,
        'chamada_iniciada_em': participacao.chamada_iniciada_em.isoformat() if participacao.chamada_iniciada_em else None,
        'chamada_expira_em': participacao.chamada_expira_em.isoformat() if participacao.chamada_expira_em else None,
        'horario_servidor': agora.isoformat(),
        'tempo_restante_segundos': tempo_restante_segundos,
        'barbeiro': {
            'id': participacao.barbeiro_id,
            'nome_exibicao': participacao.barbeiro.nome_exibicao,
            'foto_url': participacao.barbeiro.foto_url,
            'online': participacao.barbeiro.online,
        }
    }
    return resposta_json_no_store(dados_acompanhamento)


@require_POST
@tratar_excecoes_fila
def cliente_sair_da_fila(request: HttpRequest) -> HttpResponse:
    """Permite ao próprio cliente sair da fila quando estiver AGUARDANDO ou CHAMADO."""
    token_cookie = request.COOKIES.get(settings.FILA_COOKIE_NOME)
    participacao = buscar_participacao_por_token(token_cookie)

    if not participacao:
        resp = resposta_json_no_store({'mensagem': "Você não possui uma participação ativa na fila."}, status=200)
        resp.delete_cookie(settings.FILA_COOKIE_NOME, path='/')
        return resp

    sair_da_fila(participacao)

    resp = resposta_json_no_store({'mensagem': "Saída da fila realizada com sucesso."})
    resp.delete_cookie(settings.FILA_COOKIE_NOME, path='/')
    return resp


# ==============================================================================
# 3. ENDPOINTS DO BARBEIRO
# ==============================================================================

@require_POST
@tratar_excecoes_fila
def barbeiro_login(request: HttpRequest) -> JsonResponse:
    """
    Login do barbeiro com controle de taxa persistente por IP (Passo 15).
    30 tentativas POST em 10 minutos -> 30 minutos de bloqueio.
    """
    ip = obter_ip_cliente(request)
    # Valida e contabiliza tentativa de login
    registrar_e_validar_tentativa_login(ip, escopo='barbeiro')

    dados = extrair_json(request)
    usuario = dados.get('username', '').strip()
    senha = dados.get('senha', '')

    user = authenticate(request, username=usuario, password=senha)
    if user is None or not user.is_active:
        return resposta_json_no_store({'erro': "Credenciais inválidas ou usuário inativo."}, status=401)

    if not hasattr(user, 'barbeiro') or not user.barbeiro.ativo:
        return resposta_json_no_store({'erro': "Credenciais inválidas ou usuário inativo."}, status=401)

    login(request, user)
    return resposta_json_no_store({
        'mensagem': "Login realizado com sucesso.",
        'barbeiro': {
            'id': user.barbeiro.id,
            'nome_exibicao': user.barbeiro.nome_exibicao,
            'foto_url': user.barbeiro.foto_url,
            'online': user.barbeiro.online,
        }
    })


@require_POST
def barbeiro_logout(request: HttpRequest) -> JsonResponse:
    """Logout da sessão do barbeiro."""
    logout(request)
    return resposta_json_no_store({'mensagem': "Logout realizado com sucesso."})


@require_GET
@requer_barbeiro_autenticado
def barbeiro_painel_operacional(request: HttpRequest, barbeiro: Barbeiro) -> JsonResponse:
    """Painel operacional com os dados dos clientes da sua cadeira (Passo 10)."""
    participacoes_qs = ParticipacaoFila.objects.filter(barbeiro=barbeiro).order_by('ordem_fila', 'pk')

    participantes = []
    agora = timezone.now()
    for p in participacoes_qs:
        participantes.append({
            'id': p.id,
            'identificador_publico': str(p.identificador_publico),
            'nome_completo': p.nome_completo,
            'telefone': p.telefone,
            'idade': p.idade,
            'status': p.status,
            'ordem_fila': p.ordem_fila,
            'entrou_na_fila_em': p.entrou_na_fila_em.isoformat(),
            'chamada_iniciada_em': p.chamada_iniciada_em.isoformat() if p.chamada_iniciada_em else None,
            'chamada_expira_em': p.chamada_expira_em.isoformat() if p.chamada_expira_em else None,
            'identificador_chamada': str(p.identificador_chamada) if p.identificador_chamada else None,
            'iniciado_em': p.iniciado_em.isoformat() if p.iniciado_em else None,
            'ultrapassagens_por_falta': p.ultrapassagens_por_falta,
            'aguardando_ultrapassagem': p.aguardando_ultrapassagem,
        })

    return resposta_json_no_store({
        'barbeiro': {
            'id': barbeiro.id,
            'nome_exibicao': barbeiro.nome_exibicao,
            'foto_url': barbeiro.foto_url,
            'ativo': barbeiro.ativo,
            'online': barbeiro.online,
            'proxima_ordem_fila': barbeiro.proxima_ordem_fila,
        },
        'horario_servidor': agora.isoformat(),
        'participantes': participantes,
    })


@require_POST
@requer_barbeiro_autenticado
@tratar_excecoes_fila
def barbeiro_alterar_status_cadeira(request: HttpRequest, barbeiro: Barbeiro) -> JsonResponse:
    """Alterna cadeira entre online e offline (Passo 12)."""
    dados = extrair_json(request)
    ficar_online = bool(dados.get('online', False))
    barbeiro_atualizado = alternar_status_cadeira(barbeiro, ficar_online)
    return resposta_json_no_store({
        'mensagem': f"Cadeira definida como {'online' if barbeiro_atualizado.online else 'offline'}.",
        'online': barbeiro_atualizado.online,
    })


@require_POST
@requer_barbeiro_autenticado
@tratar_excecoes_fila
def barbeiro_chamar_proximo(request: HttpRequest, barbeiro: Barbeiro) -> JsonResponse:
    """Chama o próximo elegível na ordem."""
    chamado = chamar_proximo_manual(barbeiro)
    return resposta_json_no_store({
        'mensagem': f"Participante {chamado.nome_completo} chamado.",
        'identificador_publico': str(chamado.identificador_publico),
        'identificador_chamada': str(chamado.identificador_chamada),
        'chamada_expira_em': chamado.chamada_expira_em.isoformat(),
    })


@require_POST
@requer_barbeiro_autenticado
@tratar_excecoes_fila
def barbeiro_chamar_especifico(request: HttpRequest, barbeiro: Barbeiro, participacao_id: int) -> JsonResponse:
    """Chama um cliente específico em AGUARDANDO (Passo 10)."""
    chamado = chamar_especifico(barbeiro, participacao_id)
    return resposta_json_no_store({
        'mensagem': f"Participante {chamado.nome_completo} chamado.",
        'identificador_publico': str(chamado.identificador_publico),
        'identificador_chamada': str(chamado.identificador_chamada),
        'chamada_expira_em': chamado.chamada_expira_em.isoformat(),
    })


@require_POST
@requer_barbeiro_autenticado
@tratar_excecoes_fila
def barbeiro_iniciar_atendimento(request: HttpRequest, barbeiro: Barbeiro, participacao_id: int) -> JsonResponse:
    """Inicia atendimento após chamada válida (Passo 8 e 9)."""
    dados = extrair_json(request)
    identificador_chamada_str = dados.get('identificador_chamada', '')
    if not identificador_chamada_str:
        return resposta_json_no_store({'erro': "O identificador da chamada é obrigatório."}, status=400)

    participacao = iniciar_atendimento(barbeiro, participacao_id, identificador_chamada_str)
    return resposta_json_no_store({
        'mensagem': f"Atendimento de {participacao.nome_completo} iniciado.",
        'status': participacao.status,
        'iniciado_em': participacao.iniciado_em.isoformat(),
    })


@require_POST
@requer_barbeiro_autenticado
@tratar_excecoes_fila
def barbeiro_finalizar_corte(request: HttpRequest, barbeiro: Barbeiro, participacao_id: int) -> JsonResponse:
    """Finaliza o corte em andamento e exclui a participação (Passo 11)."""
    finalizar_corte(barbeiro, participacao_id)
    return resposta_json_no_store({
        'mensagem': "Atendimento finalizado com sucesso. Cadeira liberada.",
    })


@require_POST
@requer_barbeiro_autenticado
@tratar_excecoes_fila
def barbeiro_remover_participante(request: HttpRequest, barbeiro: Barbeiro, participacao_id: int) -> JsonResponse:
    """Remove participante em espera ou chamado (Passo 11)."""
    remover_participante(request.user, participacao_id)
    return resposta_json_no_store({
        'mensagem': "Participante removido da fila com sucesso.",
    })


# ==============================================================================
# 4. ENDPOINTS DO PROPRIETÁRIO (ADMINISTRAÇÃO)
# ==============================================================================

@require_POST
@tratar_excecoes_fila
def proprietario_login(request: HttpRequest) -> JsonResponse:
    """Login administrativo do proprietário com rate limiting dedicado."""
    ip = obter_ip_cliente(request)
    registrar_e_validar_tentativa_login(ip, escopo='proprietario')

    dados = extrair_json(request)
    usuario = dados.get('username', '').strip()
    senha = dados.get('senha', '')

    user = authenticate(request, username=usuario, password=senha)
    if user is None or not user.is_active or not user.is_superuser:
        return resposta_json_no_store({'erro': "Credenciais inválidas ou usuário não autorizado."}, status=401)

    login(request, user)
    return resposta_json_no_store({'mensagem': "Login administrativo realizado com sucesso."})


@require_POST
def proprietario_logout(request: HttpRequest) -> JsonResponse:
    """Logout do proprietário."""
    logout(request)
    return resposta_json_no_store({'mensagem': "Logout administrativo realizado com sucesso."})


@require_GET
@requer_proprietario_autenticado
def proprietario_visao_geral(request: HttpRequest) -> JsonResponse:
    """Visão geral executiva de todas as cadeiras e filas ativas (Passo 16)."""
    cadeiras = listar_cadeiras_publicas()
    total_aguardando = sum(c['quantidade_aguardando'] for c in cadeiras)
    total_em_atendimento = sum(1 for c in cadeiras if c['atendimento_em_andamento'])

    return resposta_json_no_store({
        'horario_servidor': timezone.now().isoformat(),
        'estatisticas': {
            'total_cadeiras': len(cadeiras),
            'total_aguardando': total_aguardando,
            'total_em_atendimento': total_em_atendimento,
        },
        'cadeiras': cadeiras,
    })


@require_GET
@requer_proprietario_autenticado
def proprietario_listar_barbeiros(request: HttpRequest) -> JsonResponse:
    """Lista todos os barbeiros cadastrados no sistema."""
    barbeiros = Barbeiro.objects.select_related('usuario').all().order_by('nome_exibicao')
    lista = []
    for b in barbeiros:
        lista.append({
            'id': b.id,
            'username': b.usuario.username,
            'nome_exibicao': b.nome_exibicao,
            'foto_url': b.foto_url,
            'ativo': b.ativo,
            'online': b.online,
            'usuario_ativo': b.usuario.is_active,
            'criado_em': b.criado_em.isoformat(),
        })
    return resposta_json_no_store({'barbeiros': lista})


@require_POST
@requer_proprietario_autenticado
@tratar_excecoes_fila
def proprietario_cadastrar_barbeiro(request: HttpRequest) -> JsonResponse:
    """Cadastra um novo barbeiro com conta padrão sem privilégios administrativos (Passo 16)."""
    dados = request.POST if request.content_type == 'multipart/form-data' else extrair_json(request)
    username = dados.get('username', '').strip()
    senha = dados.get('senha', '')
    nome_exibicao = dados.get('nome_exibicao', '').strip()

    if not username or not senha or not nome_exibicao:
        return resposta_json_no_store({'erro': "Nome de usuário, senha e nome de exibição são obrigatórios."}, status=400)

    if User.objects.filter(username=username).exists():
        return resposta_json_no_store({'erro': "Nome de usuário já existe."}, status=409)

    candidato = User(username=username)
    candidato.full_clean(exclude=['password'])
    validate_password(senha, candidato)
    if len(nome_exibicao) > 100:
        raise ValidacaoFilaError("O nome deve ter no máximo 100 caracteres.")
    def criar():
        user = User.objects.create_user(username=username, password=senha,
            is_staff=False, is_superuser=False, is_active=True)
        return Barbeiro.objects.create(usuario=user, nome_exibicao=nome_exibicao,
            ativo=True, online=False, proxima_ordem_fila=1, foto=request.FILES.get('foto', ''))
    barbeiro = executar_com_retentativas_sqlite(criar)

    return resposta_json_no_store({
        'mensagem': f"Barbeiro {nome_exibicao} cadastrado com sucesso.",
        'barbeiro_id': barbeiro.id,
        'foto_url': barbeiro.foto_url,
    }, status=201)


@require_POST
@requer_proprietario_autenticado
@tratar_excecoes_fila
def proprietario_editar_barbeiro(request: HttpRequest, barbeiro_id: int) -> JsonResponse:
    """Edita dados cadastrais do barbeiro (nome de exibição)."""
    try:
        barbeiro = Barbeiro.objects.get(pk=barbeiro_id)
    except Barbeiro.DoesNotExist:
        return resposta_json_no_store({'erro': "Barbeiro não encontrado."}, status=404)

    dados = request.POST if request.content_type == 'multipart/form-data' else extrair_json(request)
    novo_nome = dados.get('nome_exibicao', '').strip()
    if not novo_nome:
        return resposta_json_no_store({'erro': "O nome de exibição não pode ser vazio."}, status=400)

    if len(novo_nome) > 100:
        raise ValidacaoFilaError("O nome deve ter no máximo 100 caracteres.")
    def editar():
        barbeiro.refresh_from_db()
        barbeiro.nome_exibicao = novo_nome
        if request.FILES.get('foto'):
            barbeiro.foto = request.FILES['foto']
        elif dados.get('remover_foto') in (True, 'true', 'on'):
            barbeiro.foto = ''
        barbeiro.save(update_fields=['nome_exibicao', 'foto'])
    executar_com_retentativas_sqlite(editar)
    return resposta_json_no_store({'mensagem': 'Perfil atualizado com sucesso.', 'foto_url': barbeiro.foto_url})


@require_POST
@requer_proprietario_autenticado
@tratar_excecoes_fila
def proprietario_redefinir_senha_barbeiro(request: HttpRequest, barbeiro_id: int) -> JsonResponse:
    """Redefine a senha de acesso de um barbeiro (Passo 16)."""
    try:
        barbeiro = Barbeiro.objects.select_related('usuario').get(pk=barbeiro_id)
    except Barbeiro.DoesNotExist:
        return resposta_json_no_store({'erro': "Barbeiro não encontrado."}, status=404)

    dados = extrair_json(request)
    nova_senha = dados.get('nova_senha', '')
    if not nova_senha or len(nova_senha) < 6:
        return resposta_json_no_store({'erro': "A nova senha deve possuir no mínimo 6 caracteres."}, status=400)

    validate_password(nova_senha, barbeiro.usuario)
    barbeiro.usuario.set_password(nova_senha)
    barbeiro.usuario.save(update_fields=['password'])
    return resposta_json_no_store({'mensagem': f"Senha do barbeiro '{barbeiro.nome_exibicao}' redefinida com sucesso."})


@require_POST
@requer_proprietario_autenticado
@tratar_excecoes_fila
def proprietario_alternar_status_barbeiro(request: HttpRequest, barbeiro_id: int) -> JsonResponse:
    """
    Ativa ou desativa um barbeiro (Passo 16).
    Ao desativar: rejeita se em atendimento, limpa a fila, coloca offline e desativa conta.
    """
    try:
        barbeiro = Barbeiro.objects.get(pk=barbeiro_id)
    except Barbeiro.DoesNotExist:
        return resposta_json_no_store({'erro': "Barbeiro não encontrado."}, status=404)

    dados = extrair_json(request)
    ativar = bool(dados.get('ativo', False))

    if ativar:
        def reativar():
            barbeiro.refresh_from_db()
            barbeiro.ativo = True
            barbeiro.usuario.is_active = True
            barbeiro.save(update_fields=['ativo'])
            barbeiro.usuario.save(update_fields=['is_active'])
        executar_com_retentativas_sqlite(reativar)
        mensagem = f"Barbeiro '{barbeiro.nome_exibicao}' ativado com sucesso."
    else:
        desativar_barbeiro(barbeiro)
        barbeiro.refresh_from_db()
        mensagem = f"Barbeiro '{barbeiro.nome_exibicao}' desativado e fila limpa com sucesso."

    return resposta_json_no_store({'mensagem': mensagem, 'ativo': barbeiro.ativo})


@require_GET
@requer_proprietario_autenticado
def proprietario_filas_ativas(request: HttpRequest) -> JsonResponse:
    """Consulta todas as participações ativas de todas as cadeiras (Passo 16)."""
    participacoes = ParticipacaoFila.objects.select_related('barbeiro').all().order_by('barbeiro__nome_exibicao', 'ordem_fila')
    lista = []
    for p in participacoes:
        lista.append({
            'id': p.id,
            'barbeiro_id': p.barbeiro.id,
            'barbeiro_nome': p.barbeiro.nome_exibicao,
            'identificador_publico': str(p.identificador_publico),
            'nome_completo': p.nome_completo,
            'telefone': p.telefone,
            'idade': p.idade,
            'status': p.status,
            'ordem_fila': p.ordem_fila,
            'entrou_na_fila_em': p.entrou_na_fila_em.isoformat(),
            'chamada_expira_em': p.chamada_expira_em.isoformat() if p.chamada_expira_em else None,
            'ultrapassagens_por_falta': p.ultrapassagens_por_falta,
        })
    return resposta_json_no_store({'participacoes': lista})


@require_POST
@requer_proprietario_autenticado
@tratar_excecoes_fila
def proprietario_remover_participante(request: HttpRequest, participacao_id: int) -> JsonResponse:
    """Proprietário remove qualquer participante em AGUARDANDO ou CHAMADO."""
    remover_participante(request.user, participacao_id)
    return resposta_json_no_store({'mensagem': "Participante removido da fila pelo proprietário com sucesso."})
