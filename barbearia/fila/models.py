"""
Modelos do sistema de fila para barbearia (Guild Tech Support).
Backend 100% Python, Django e SQLite.
"""

import uuid
from django.conf import settings
from django.db import models, transaction
from fila.fotos import caminho_foto, validar_foto, preparar_foto
from django.db.models import Q, F
from django.utils import timezone


class Barbeiro(models.Model):
    """
    Representa o barbeiro associado a uma cadeira de atendimento (relação 1:1 no MVP).
    """
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='barbeiro',
        verbose_name="Usuário de acesso"
    )
    nome_exibicao = models.CharField(
        max_length=100,
        verbose_name="Nome de exibição"
    )
    foto = models.ImageField(upload_to=caminho_foto, blank=True,
        validators=[validar_foto], verbose_name="Foto do barbeiro")

    def save(self, *args, **kwargs):
        atualiza_foto = kwargs.get('update_fields') is None or 'foto' in kwargs['update_fields']
        if not atualiza_foto:
            return super().save(*args, **kwargs)
        nova = preparar_foto(self.foto) if self.foto and not self.foto._committed else None
        if nova is not None:
            self.foto = nova
        storage = self._meta.get_field('foto').storage
        try:
            with transaction.atomic():
                antiga = type(self).objects.filter(pk=self.pk).values_list('foto', flat=True).first() if self.pk else None
                resultado = super().save(*args, **kwargs)
                if antiga and antiga != self.foto.name:
                    transaction.on_commit(lambda: storage.delete(antiga))
                return resultado
        except Exception:
            if nova is not None and self.foto._committed:
                storage.delete(self.foto.name)
                self.foto = nova
            raise

    @property
    def foto_url(self):
        return self.foto.url if self.foto else None

    ativo = models.BooleanField(
        default=True,
        verbose_name="Ativo"
    )
    online = models.BooleanField(
        default=False,
        verbose_name="Online"
    )
    proxima_ordem_fila = models.PositiveIntegerField(
        default=1,
        verbose_name="Próxima ordem na fila"
    )
    criado_em = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Criado em"
    )
    atualizado_em = models.DateTimeField(
        auto_now=True,
        verbose_name="Atualizado em"
    )

    class Meta:
        verbose_name = "Barbeiro"
        verbose_name_plural = "Barbeiros"
        ordering = ['nome_exibicao']

    def __str__(self):
        status_str = "Online" if self.online else "Offline"
        if not self.ativo:
            status_str = "Desativado"
        return f"{self.nome_exibicao} ({status_str})"

    @property
    def aceita_entrada(self) -> bool:
        """Indica se a cadeira aceita novas entradas na fila."""
        return self.ativo and self.online


class StatusParticipacao(models.TextChoices):
    AGUARDANDO = 'AGUARDANDO', 'Aguardando'
    CHAMADO = 'CHAMADO', 'Chamado'
    EM_ATENDIMENTO = 'EM_ATENDIMENTO', 'Em atendimento'


class ParticipacaoFila(models.Model):
    """
    Participação temporária de um cliente na fila de uma cadeira.
    Não existe cadastro permanente de cliente nem histórico após encerramento.
    """
    barbeiro = models.ForeignKey(
        Barbeiro,
        on_delete=models.PROTECT,
        related_name='participacoes',
        verbose_name="Barbeiro"
    )
    identificador_publico = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
        verbose_name="Identificador público"
    )
    primeiro_nome = models.CharField(
        max_length=50,
        verbose_name="Primeiro nome"
    )
    sobrenome = models.CharField(
        max_length=100,
        verbose_name="Sobrenome"
    )
    telefone = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        verbose_name="Telefone normalizado"
    )
    data_nascimento = models.DateField(
        verbose_name="Data de nascimento"
    )
    status = models.CharField(
        max_length=20,
        choices=StatusParticipacao.choices,
        default=StatusParticipacao.AGUARDANDO,
        verbose_name="Status"
    )
    ordem_fila = models.PositiveIntegerField(
        verbose_name="Ordem na fila"
    )
    entrou_na_fila_em = models.DateTimeField(
        default=timezone.now,
        verbose_name="Entrou na fila em"
    )
    chamada_iniciada_em = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Chamada iniciada em"
    )
    chamada_expira_em = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Chamada expira em"
    )
    identificador_chamada = models.UUIDField(
        null=True,
        blank=True,
        verbose_name="Identificador único do ciclo de chamada"
    )
    iniciado_em = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Atendimento iniciado em"
    )
    ultrapassagens_por_falta = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="Ultrapassagens por falta"
    )
    aguardando_ultrapassagem = models.BooleanField(
        default=False,
        verbose_name="Aguardando ultrapassagem"
    )
    hash_token_acesso = models.CharField(
        max_length=128,
        db_index=True,
        verbose_name="Hash do token secreto de acesso"
    )

    class Meta:
        verbose_name = "Participação na Fila"
        verbose_name_plural = "Participações na Fila"
        ordering = ['ordem_fila', 'pk']
        constraints = [
            # 1. Telefone normalizado único entre todas as participações existentes
            models.UniqueConstraint(
                fields=['telefone'],
                name='unique_participacao_telefone'
            ),
            # 2. Ordem da fila única por barbeiro
            models.UniqueConstraint(
                fields=['barbeiro', 'ordem_fila'],
                name='unique_barbeiro_ordem_fila'
            ),
            # 3. Somente um CHAMADO por barbeiro
            models.UniqueConstraint(
                fields=['barbeiro'],
                condition=Q(status=StatusParticipacao.CHAMADO),
                name='unique_barbeiro_um_chamado'
            ),
            # 4. Somente um EM_ATENDIMENTO por barbeiro
            models.UniqueConstraint(
                fields=['barbeiro'],
                condition=Q(status=StatusParticipacao.EM_ATENDIMENTO),
                name='unique_barbeiro_um_em_atendimento'
            ),
            # 5. Valores de status permitidos
            models.CheckConstraint(
                check=Q(status__in=[
                    StatusParticipacao.AGUARDANDO,
                    StatusParticipacao.CHAMADO,
                    StatusParticipacao.EM_ATENDIMENTO,
                ]),
                name='check_status_valido'
            ),
            # 6. Ultrapassagens entre zero e cinco
            models.CheckConstraint(
                check=Q(ultrapassagens_por_falta__gte=0, ultrapassagens_por_falta__lte=5),
                name='check_ultrapassagens_faixa'
            ),
            # 7. chamada_expira_em posterior a chamada_iniciada_em
            models.CheckConstraint(
                check=(
                    (Q(chamada_iniciada_em__isnull=True) & Q(chamada_expira_em__isnull=True))
                    | Q(chamada_expira_em__gt=F('chamada_iniciada_em'))
                ),
                name='check_chamada_expira_apos_inicio'
            ),
            # 8. Campos de chamada presentes somente durante CHAMADO
            models.CheckConstraint(
                check=(
                    (
                        Q(status=StatusParticipacao.CHAMADO)
                        & Q(chamada_iniciada_em__isnull=False)
                        & Q(chamada_expira_em__isnull=False)
                        & Q(identificador_chamada__isnull=False)
                    )
                    | (
                        ~Q(status=StatusParticipacao.CHAMADO)
                        & Q(chamada_iniciada_em__isnull=True)
                        & Q(chamada_expira_em__isnull=True)
                        & Q(identificador_chamada__isnull=True)
                    )
                ),
                name='check_campos_chamada_apenas_em_chamado'
            ),
            # 9. iniciado_em preenchido somente durante EM_ATENDIMENTO
            models.CheckConstraint(
                check=(
                    (
                        Q(status=StatusParticipacao.EM_ATENDIMENTO)
                        & Q(iniciado_em__isnull=False)
                    )
                    | (
                        ~Q(status=StatusParticipacao.EM_ATENDIMENTO)
                        & Q(iniciado_em__isnull=True)
                    )
                ),
                name='check_iniciado_em_apenas_em_atendimento'
            ),
            # 10. iniciado_em nunca anterior à entrada
            models.CheckConstraint(
                check=(
                    Q(iniciado_em__isnull=True)
                    | Q(iniciado_em__gte=F('entrou_na_fila_em'))
                ),
                name='check_iniciado_em_nao_anterior_entrada'
            ),
        ]

    def __str__(self):
        return f"{self.nome_completo} (#{self.ordem_fila} - {self.status})"

    @property
    def nome_completo(self) -> str:
        """Calcula o nome completo pela junção de primeiro nome e sobrenome."""
        return f"{self.primeiro_nome} {self.sobrenome}".strip()

    @property
    def idade(self) -> int:
        """
        Calcula a idade dinâmica considerando a data local atual em America/Sao_Paulo
        e se o aniversário já ocorreu no ano corrente.
        """
        hoje = timezone.localdate()
        aniversario_ja_ocorreu = (
            (hoje.month, hoje.day) >= (self.data_nascimento.month, self.data_nascimento.day)
        )
        return hoje.year - self.data_nascimento.year - (0 if aniversario_ja_ocorreu else 1)


class RegistroTentativaLoginIP(models.Model):
    """
    Registro técnico persistente para controle de taxa (rate limit) de autenticação por IP.
    Não armazena dados de cliente nem histórico de navegação.
    """
    ip = models.GenericIPAddressField(
        db_index=True,
        verbose_name="Endereço IP"
    )
    escopo = models.CharField(
        max_length=20,
        default='barbeiro',
        db_index=True,
        verbose_name="Escopo do login"
    )
    tentativas = models.PositiveIntegerField(
        default=0,
        verbose_name="Contagem de tentativas na janela"
    )
    primeira_tentativa_em = models.DateTimeField(
        verbose_name="Início da janela atual"
    )
    ultima_tentativa_em = models.DateTimeField(
        verbose_name="Última tentativa realizada"
    )
    bloqueado_ate = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Bloqueado até"
    )

    class Meta:
        verbose_name = "Registro de Tentativa de Login por IP"
        verbose_name_plural = "Registros de Tentativas de Login por IP"
        unique_together = [('ip', 'escopo')]

    def __str__(self):
        return f"IP {self.ip} [{self.escopo}] - {self.tentativas} tentativas"
