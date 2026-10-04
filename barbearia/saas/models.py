"""Dados permanentes da plataforma. Dados temporários continuam em fila."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q


class Datas(models.Model):
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Barbearia(Datas):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, max_length=80)
    contato_comercial = models.CharField(max_length=120, blank=True)
    provisionamento = models.CharField(max_length=24, default='CONFIGURACAO', choices=[
        ('CONFIGURACAO', 'Configuração'), ('PENDENTE', 'Pendente'),
        ('EQUIPE', 'Configurar equipe'), ('PRONTO', 'Pronto'), ('ERRO', 'Erro')])
    situacao_administrativa = models.CharField(max_length=16, default='ATIVA', choices=[
        ('ATIVA', 'Ativa'), ('BLOQUEADA', 'Bloqueada')])
    motivo_bloqueio = models.CharField(max_length=240, blank=True)
    erro_provisionamento = models.CharField(max_length=100, blank=True)
    # Lease de integração: serializa chamadas remotas sem manter transação aberta.
    integracao_token = models.UUIDField(null=True, blank=True)
    integracao_ate = models.DateTimeField(null=True, blank=True)
    sincronizado_em = models.DateTimeField(null=True, blank=True)
    erro_integracao = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.nome


class VinculoBarbearia(Datas):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    barbearia = models.ForeignKey(Barbearia, on_delete=models.PROTECT, related_name='vinculos')
    # Barbeiro.usuario é a fonte de autorização operacional; não duplicamos esse papel.
    papel = models.CharField(max_length=16, default='DONO', choices=[('DONO', 'Dono')])
    ativo = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['usuario', 'barbearia'], name='vinculo_usuario_empresa'),
            models.UniqueConstraint(fields=['barbearia'], condition=Q(ativo=True, papel='DONO'), name='um_dono_ativo'),
            models.CheckConstraint(condition=Q(papel='DONO'), name='papel_dono_valido'),
        ]


class Plano(Datas):
    codigo = models.CharField(max_length=40, unique=True, default='mensal')
    nome = models.CharField(max_length=100, default='GTS Mensal')
    preco_centavos = models.PositiveIntegerField(default=10000)
    moeda = models.CharField(max_length=3, default='brl')
    periodicidade = models.CharField(max_length=10, default='month')
    stripe_price_id_test = models.CharField(max_length=100, blank=True)
    stripe_price_id_live = models.CharField(max_length=100, blank=True)
    disponivel = models.BooleanField(default=False)

    def price_id(self):
        return getattr(self, 'stripe_price_id_' + settings.STRIPE_AMBIENTE)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(preco_centavos=10000, moeda='brl', periodicidade='month'), name='plano_mvp')]


class ClienteCobranca(Datas):
    barbearia = models.ForeignKey(Barbearia, on_delete=models.PROTECT, related_name='clientes_cobranca')
    ambiente = models.CharField(max_length=4)
    stripe_customer_id = models.CharField(max_length=100, null=True, blank=True)
    chave_idempotencia = models.UUIDField(default=uuid.uuid4, unique=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['barbearia', 'ambiente'], name='cliente_por_ambiente'),
            models.UniqueConstraint(fields=['ambiente', 'stripe_customer_id'], name='customer_unico'),
        ]


class Assinatura(Datas):
    barbearia = models.ForeignKey(Barbearia, on_delete=models.PROTECT, related_name='assinaturas')
    plano = models.ForeignKey(Plano, on_delete=models.PROTECT)
    ambiente = models.CharField(max_length=4)
    stripe_subscription_id = models.CharField(max_length=100)
    status_stripe = models.CharField(max_length=32)
    encerrada = models.BooleanField(default=False)
    cancelar_ao_final_do_periodo = models.BooleanField(default=False)
    periodo_inicio = models.DateTimeField(null=True, blank=True)
    periodo_fim = models.DateTimeField(null=True, blank=True)
    acesso_pago_desde = models.DateTimeField(null=True, blank=True)
    acesso_pago_ate = models.DateTimeField(null=True, blank=True)
    ultima_fatura_paga = models.CharField(max_length=100, blank=True)
    sincronizado_em = models.DateTimeField(null=True, blank=True)
    situacao_local = models.CharField(max_length=32, default='PENDENTE')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['ambiente', 'stripe_subscription_id'], name='subscription_unica'),
            models.UniqueConstraint(fields=['barbearia', 'ambiente'], condition=Q(encerrada=False), name='uma_assinatura_aberta'),
        ]


class TentativaContratacao(Datas):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    barbearia = models.ForeignKey(Barbearia, on_delete=models.PROTECT, related_name='contratacoes')
    plano = models.ForeignKey(Plano, on_delete=models.PROTECT)
    ambiente = models.CharField(max_length=4)
    stripe_price_id = models.CharField(max_length=100)
    stripe_checkout_session_id = models.CharField(max_length=100, null=True, blank=True)
    situacao = models.CharField(max_length=24, default='CRIANDO')
    aberta = models.BooleanField(default=True)
    expiracao = models.DateTimeField()
    chave_idempotencia = models.UUIDField(default=uuid.uuid4, unique=True)
    url = models.URLField(max_length=2048, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['ambiente', 'stripe_checkout_session_id'], name='checkout_unico'),
            models.UniqueConstraint(fields=['barbearia', 'ambiente'], condition=Q(aberta=True), name='uma_contratacao_aberta'),
        ]


class EventoStripe(Datas):
    ambiente = models.CharField(max_length=4)
    stripe_event_id = models.CharField(max_length=100)
    tipo = models.CharField(max_length=100)
    objeto_id = models.CharField(max_length=100)
    recebido_em = models.DateTimeField(auto_now_add=True)
    situacao = models.CharField(max_length=24, default='PENDENTE')
    tentativas = models.PositiveIntegerField(default=0)
    erro_tecnico = models.CharField(max_length=100, blank=True)
    processado_em = models.DateTimeField(null=True, blank=True)
    proxima_tentativa_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['ambiente', 'stripe_event_id'], name='evento_stripe_unico')]


class AcaoPlataforma(Datas):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    barbearia = models.ForeignKey(Barbearia, null=True, on_delete=models.PROTECT)
    acao = models.CharField(max_length=40)
    motivo = models.CharField(max_length=240, blank=True)
