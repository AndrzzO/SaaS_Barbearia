from dataclasses import asdict
from functools import wraps
from django.conf import settings
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import transaction, IntegrityError
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from allauth.socialaccount.models import SocialAccount
import stripe
from fila.views import extrair_json, resposta_json_no_store, tratar_excecoes_fila
from .models import Barbearia, VinculoBarbearia, TentativaContratacao, Assinatura, EventoStripe, AcaoPlataforma
from .acesso import direito_financeiro, acesso_operacional, aplicar_suspensao
from . import billing


def conta_autenticada(view):
    @wraps(view)
    @tratar_excecoes_fila
    def wrapped(request, *args, **kwargs):
        u = request.user
        if not u.is_authenticated:
            return resposta_json_no_store({'erro': 'Entre com Google.', 'login_url': '/accounts/google/login/'}, 401)
        if (not u.is_active or u.is_staff or u.is_superuser or hasattr(u, 'barbeiro')
                or not SocialAccount.objects.filter(user=u, provider='google').exists()):
            return resposta_json_no_store({'erro': 'Conta de dono necessária.'}, 403)
        try:
            return view(request, *args, **kwargs)
        except billing.IntegracaoError as exc:
            return resposta_json_no_store({'erro': str(exc)}, 409)
        except stripe.StripeError:
            return resposta_json_no_store({'erro': 'STRIPE_INDISPONIVEL', 'recuperavel': True}, 503)
        except ObjectDoesNotExist:
            return resposta_json_no_store({'erro': 'Configuração ou cadastro ainda não disponível.'}, 409)
    return wrapped


def empresa_do_dono(request):
    vinculos = list(VinculoBarbearia.objects.select_related('barbearia').filter(usuario=request.user, ativo=True, papel='DONO')[:2])
    if len(vinculos) != 1:
        raise billing.IntegracaoError('CADASTRO_NECESSARIO' if not vinculos else 'MULTIPLOS_VINCULOS_REQUER_SELECAO')
    return vinculos[0].barbearia


@require_GET
@ensure_csrf_cookie
def comercial(request):
    return render(request, 'saas/inicio.html')


@require_GET
@ensure_csrf_cookie
@conta_autenticada
def conta(request):
    if not VinculoBarbearia.objects.filter(usuario=request.user, ativo=True).exists():
        return resposta_json_no_store({'estado': 'CADASTRO_BARBEARIA', 'cadastro_url': '/conta/barbearia/'})
    b = empresa_do_dono(request)
    financeiro = direito_financeiro(b)
    tentativa = TentativaContratacao.objects.filter(barbearia=b, ambiente=settings.STRIPE_AMBIENTE, aberta=True).first()
    a = Assinatura.objects.filter(barbearia=b, ambiente=settings.STRIPE_AMBIENTE).order_by('encerrada', '-criado_em').first()
    estado = financeiro.estado
    if estado == 'SEM_ASSINATURA' and tentativa:
        estado = 'CHECKOUT_PENDENTE'
    elif financeiro.permitido and b.provisionamento != 'PRONTO':
        estado = 'CONFIGURAR_EQUIPE' if b.provisionamento == 'EQUIPE' else 'PROVISIONAMENTO_PENDENTE'
    return resposta_json_no_store({'estado': estado, 'barbearia': {'id': str(b.pk), 'nome': b.nome, 'slug': b.slug,
        'provisionamento': b.provisionamento}, 'financeiro': asdict(financeiro),
        'operacao': asdict(acesso_operacional(b.pk)), 'acesso_pago_ate': a.acesso_pago_ate if a else None,
        'cancelamento_agendado': a.cancelar_ao_final_do_periodo if a else False,
        'acao_necessaria': 'REGULARIZAR_NO_PORTAL' if estado in ('PAGAMENTO_PENDENTE', 'TOLERANCIA', 'SUSPENSA') else None,
        'erro_recuperavel': b.erro_integracao or b.erro_provisionamento or None,
        'pode_contratar': not tentativa and (not a or a.encerrada) and b.situacao_administrativa == 'ATIVA',
        'checkout_url': tentativa.url if tentativa and tentativa.situacao == 'ABERTA' else None,
        'publico_url': '/b/' + b.slug + '/', 'equipe_url': '/b/' + b.slug + '/api/proprietario/barbeiros/'})


@require_POST
@conta_autenticada
def cadastrar_barbearia(request):
    data = extrair_json(request)
    nome = data.get('nome', '').strip()
    slug = data.get('slug', '').strip()
    if not nome or not slug or slug != slugify(slug):
        raise ValidationError('Informe nome e slug público válido em letras minúsculas.')
    with transaction.atomic():
        if VinculoBarbearia.objects.filter(usuario=request.user, ativo=True).exists():
            raise billing.IntegracaoError('BARBEARIA_JA_CADASTRADA')
        b = Barbearia(nome=nome, slug=slug, contato_comercial=data.get('contato_comercial', '').strip())
        b.full_clean()
        b.save()
        VinculoBarbearia.objects.create(usuario=request.user, barbearia=b)
    return resposta_json_no_store({'id': str(b.pk), 'estado': 'SEM_ASSINATURA'}, 201)


@require_POST
@conta_autenticada
def checkout(request):
    t = billing.criar_checkout(empresa_do_dono(request))
    return resposta_json_no_store({'url': t.url, 'estado': t.situacao})


@require_POST
@conta_autenticada
def portal(request):
    return resposta_json_no_store({'url': billing.criar_portal(empresa_do_dono(request))})


@require_POST
@conta_autenticada
def cancelar(request):
    billing.alterar_cancelamento(empresa_do_dono(request), True)
    return resposta_json_no_store({'estado': 'CANCELAMENTO_AGENDADO'})


@require_POST
@conta_autenticada
def retomar(request):
    billing.alterar_cancelamento(empresa_do_dono(request), False)
    return resposta_json_no_store({'estado': 'CANCELAMENTO_DESFEITO'})


@csrf_exempt
@require_POST
def webhook(request):
    try:
        billing.receber_evento(request.body, request.headers.get('Stripe-Signature', ''))
    except (ValueError, stripe.SignatureVerificationError):
        return HttpResponse(status=400)
    except billing.IntegracaoError:
        return HttpResponse(status=400)
    # Só confirma depois da persistência. Falha de banco resulta em 500/reenvio Stripe.
    return HttpResponse(status=200)


def plataforma_autenticada(view):
    @wraps(view)
    @tratar_excecoes_fila
    def wrapped(request, *args, **kwargs):
        u = request.user
        if not (u.is_authenticated and u.is_active and u.is_staff and u.is_superuser):
            return resposta_json_no_store({'erro': 'Administração GTS necessária.'}, 403)
        return view(request, *args, **kwargs)
    return wrapped


@require_GET
@ensure_csrf_cookie
@plataforma_autenticada
def plataforma(request):
    try:
        offset = max(0, int(request.GET.get('offset', 0)))
    except ValueError:
        return resposta_json_no_store({'erro': 'Offset inválido.'}, 400)
    empresas = []
    for b in Barbearia.objects.order_by('criado_em')[offset:offset + 50]:
        a = b.assinaturas.filter(ambiente=settings.STRIPE_AMBIENTE).order_by('encerrada', '-criado_em').first()
        empresas.append({'id': str(b.pk), 'nome': b.nome, 'slug': b.slug, 'provisionamento': b.provisionamento,
            'donos': list(b.vinculos.filter(ativo=True).values_list('usuario_id', flat=True)),
            'administrativo': b.situacao_administrativa, 'acesso': asdict(direito_financeiro(b)),
            'erro_integracao': b.erro_integracao, 'erro_provisionamento': b.erro_provisionamento,
            'pago_ate': a.acesso_pago_ate if a else None,
            'cancelamento_agendado': a.cancelar_ao_final_do_periodo if a else False})
    return resposta_json_no_store({'empresas': empresas, 'proximo_offset': offset + 50 if len(empresas) == 50 else None,
        'eventos': list(EventoStripe.objects.exclude(situacao='PROCESSADO').order_by('pk')[:50].values(
            'id', 'tipo', 'situacao', 'tentativas', 'erro_tecnico'))})


@require_POST
@plataforma_autenticada
def bloquear(request, barbearia_id):
    data = extrair_json(request)
    if not isinstance(data.get('ativo'), bool) or not data.get('motivo', '').strip():
        raise ValidationError('Informe ativo (booleano) e motivo.')
    with transaction.atomic():
        b = Barbearia.objects.get(pk=barbearia_id)
        b.situacao_administrativa = 'ATIVA' if data['ativo'] else 'BLOQUEADA'
        b.motivo_bloqueio = data['motivo'].strip()[:240]
        b.save()
        aplicar_suspensao(b.pk)
        AcaoPlataforma.objects.create(usuario=request.user, barbearia=b, acao=b.situacao_administrativa, motivo=b.motivo_bloqueio)
    return resposta_json_no_store({'estado': b.situacao_administrativa})


@require_POST
@plataforma_autenticada
def sincronizar(request, barbearia_id):
    try:
        billing.sincronizar_empresa(barbearia_id)
    except Exception as exc:
        return resposta_json_no_store({'erro': billing.erro_seguro(exc)}, 409)
    AcaoPlataforma.objects.create(usuario=request.user, barbearia_id=barbearia_id, acao='SINCRONIZAR')
    return resposta_json_no_store({'estado': 'SINCRONIZADO'})


@require_POST
@plataforma_autenticada
def reprocessar(request, evento_id):
    EventoStripe.objects.filter(pk=evento_id, situacao='ERRO').update(situacao='PENDENTE', tentativas=0, proxima_tentativa_em=None)
    AcaoPlataforma.objects.create(usuario=request.user, acao='REPROCESSAR_EVENTO', motivo=str(evento_id))
    return resposta_json_no_store({'estado': 'AGENDADO'})
