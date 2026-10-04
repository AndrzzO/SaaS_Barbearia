"""Decisão local de acesso; nunca chama a Stripe durante polling."""
from dataclasses import dataclass
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from .models import Assinatura, Barbearia, VinculoBarbearia


@dataclass(frozen=True)
class Direito:
    permitido: bool
    estado: str
    limite: object = None


def dono(usuario, barbearia_id):
    return bool(usuario.is_authenticated and usuario.is_active and not usuario.is_staff
                and not usuario.is_superuser and VinculoBarbearia.objects.filter(
                    usuario=usuario, barbearia_id=barbearia_id, ativo=True, papel='DONO').exists())


def direito_financeiro(barbearia, agora=None):
    agora = agora or timezone.now()
    if barbearia.situacao_administrativa != 'ATIVA':
        return Direito(False, 'BLOQUEIO_ADMINISTRATIVO')
    a = Assinatura.objects.filter(barbearia=barbearia, ambiente=settings.STRIPE_AMBIENTE).order_by('encerrada', '-criado_em').first()
    if not a:
        return Direito(False, 'SEM_ASSINATURA')
    if a.encerrada or a.status_stripe in ('canceled', 'unpaid', 'incomplete_expired', 'paused'):
        return Direito(False, 'ENCERRADA' if a.encerrada else 'SUSPENSA')
    if not a.acesso_pago_ate or not a.acesso_pago_desde or a.acesso_pago_desde > agora:
        return Direito(False, 'PAGAMENTO_PENDENTE')
    if a.status_stripe not in ('active', 'past_due'):
        return Direito(False, 'PAGAMENTO_PENDENTE')
    if agora < a.acesso_pago_ate:
        return Direito(True, 'CANCELAMENTO_AGENDADO' if a.cancelar_ao_final_do_periodo else 'ATIVA', a.acesso_pago_ate)
    limite = a.acesso_pago_ate + timedelta(days=settings.SAAS_TOLERANCIA_DIAS)
    if a.status_stripe == 'past_due' and not a.cancelar_ao_final_do_periodo and agora < limite:
        return Direito(True, 'TOLERANCIA', limite)
    return Direito(False, 'SUSPENSA', a.acesso_pago_ate)


def acesso_operacional(barbearia_id):
    b = Barbearia.objects.get(pk=barbearia_id)
    d = direito_financeiro(b)
    if not d.permitido:
        return d
    if b.provisionamento != 'PRONTO':
        return Direito(False, 'CONFIGURAR_EQUIPE' if b.provisionamento == 'EQUIPE' else 'PROVISIONAMENTO_PENDENTE')
    return d


def exigir_operacao(barbearia_id):
    from fila.services import ConflitoEstadoError
    d = acesso_operacional(barbearia_id)
    if not d.permitido:
        raise ConflitoEstadoError('Operação indisponível: ' + d.estado)


def provisionar(barbearia_id):
    from django.db import transaction
    from fila.models import Barbeiro
    with transaction.atomic():
        b = Barbearia.objects.get(pk=barbearia_id)
        if not direito_financeiro(b).permitido:
            return
        if not b.vinculos.filter(ativo=True, papel='DONO', usuario__is_active=True,
                                 usuario__is_staff=False, usuario__is_superuser=False).exists():
            b.provisionamento = 'ERRO'
            b.erro_provisionamento = 'DONO_AUSENTE'
        else:
            b.provisionamento = 'PRONTO' if Barbeiro.objects.filter(barbearia=b, ativo=True).exists() else 'EQUIPE'
            b.erro_provisionamento = ''
        b.save(update_fields=['provisionamento', 'erro_provisionamento', 'atualizado_em'])


def aplicar_suspensao(barbearia_id):
    """Desliga sem executar a exclusão própria do desligamento manual da cadeira."""
    from fila.models import Barbeiro
    if not acesso_operacional(barbearia_id).permitido:
        Barbeiro.objects.filter(barbearia_id=barbearia_id, online=True).update(online=False)
