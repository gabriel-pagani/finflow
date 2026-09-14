from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone


class Role(models.TextChoices):
    USER = 'user', 'Usuário'
    ASSISTANT = 'assistant', 'Assistente'
    TOOL = 'tool', 'Ferramenta'


class AttachmentKind(models.TextChoices):
    IMAGE = 'image', 'Imagem'
    AUDIO = 'audio', 'Áudio'


class Kind(models.TextChoices):
    CARD = 'card', 'Cartão'
    TRANSACTION = 'transaction', 'Transação'
    INSTALLMENT = 'installment', 'Parcelamento'
    TRANSFER = 'transfer', 'Transferência'


class Action(models.TextChoices):
    CREATE = 'create', 'Criar'
    UPDATE = 'update', 'Editar'
    DELETE = 'delete', 'Apagar'


class Status(models.TextChoices):
    PENDING = 'pending', 'Aguardando Confirmação'
    CONFIRMED = 'confirmed', 'Confirmada'
    CANCELLED = 'cancelled', 'Descartada'
    FAILED = 'failed', 'Recusada na Confirmação'


class Conversation(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversation', verbose_name='Usuário')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Data e Hora da Atualização')

    def __str__(self):
        return f'Conversa de {self.user}'

    class Meta:
        verbose_name = 'Conversa'
        verbose_name_plural = 'Conversas'
        permissions = [
            ('use_assistant', 'Can use the assistant'),
        ]


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages', verbose_name='Conversa')
    role = models.CharField(max_length=20, choices=Role.choices, verbose_name='Papel')
    content = models.TextField(blank=True, verbose_name='Conteúdo')
    # Os itens exatamente como a API os devolve e espera de volta, raciocínio
    # cifrado incluído: sem ele o modelo refaz a consulta que acabou de fazer.
    items = models.JSONField(default=list, blank=True, verbose_name='Itens')
    # Falso no que só o modelo precisa ler, como o aviso de que o usuário
    # confirmou uma proposta.
    visible = models.BooleanField(default=True, verbose_name='Aparece no Chat')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')

    def __str__(self):
        return f'{self.get_role_display()}: {self.content[:60]}'

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=Role.values),
                name='message_role_within_choices',
                violation_error_message='O papel precisa ser uma das opções previstas.',
            ),
        ]
        verbose_name = 'Mensagem'
        verbose_name_plural = 'Mensagens'


# O nome que veio do navegador não entra no caminho: é escolhido por quem envia.
def attachment_path(instance, filename):
    return f'assistant/{instance.message.conversation.user_id}/{uuid4().hex}{Path(filename).suffix}'


class Attachment(models.Model):
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='attachment', verbose_name='Mensagem')
    kind = models.CharField(max_length=10, choices=AttachmentKind.choices, verbose_name='Tipo')
    file = models.FileField(upload_to=attachment_path, max_length=200, verbose_name='Arquivo')
    # O tipo que a inspeção dos bytes confirmou, e não o que o navegador disse.
    mime = models.CharField(max_length=60, verbose_name='Tipo de Conteúdo')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')

    def __str__(self):
        return f'{self.get_kind_display()} de {self.message.conversation.user}'

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=AttachmentKind.values),
                name='attachment_kind_within_choices',
                violation_error_message='O tipo precisa ser uma das opções previstas.',
            ),
        ]
        verbose_name = 'Anexo'
        verbose_name_plural = 'Anexos'


# O Django não apaga o arquivo junto da linha, e limpar a conversa deixaria o
# comprovante no volume.
@receiver(post_delete, sender=Attachment)
def remove_attachment_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)


class Proposal(models.Model):
    # Entre propor e clicar o cadastro muda e o usuário esquece do que se
    # tratava; proposta velha se pede de novo.
    EXPIRY = timedelta(hours=1)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='proposals', verbose_name='Usuário')
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='proposals', verbose_name='Conversa')
    kind = models.CharField(max_length=20, choices=Kind.choices, verbose_name='Registro')
    action = models.CharField(max_length=20, choices=Action.choices, verbose_name='Ação')
    target_id = models.PositiveBigIntegerField(blank=True, null=True, verbose_name='Id do Registro')
    # Os dados que o formulário da tela valida de novo na confirmação: o que se
    # grava é o que o card mostrou, e não uma nova leitura do que foi dito.
    payload = models.JSONField(default=dict, blank=True, verbose_name='Dados')
    # O registro como estava na proposta. Se mudou até o clique, a confirmação
    # é recusada em vez de gravar por cima do que o usuário não viu.
    snapshot = models.JSONField(default=dict, blank=True, verbose_name='Registro na Proposta')
    summary = models.JSONField(verbose_name='Resumo Exibido')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name='Situação')
    result = models.CharField(max_length=300, blank=True, verbose_name='Resultado')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')
    resolved_at = models.DateTimeField(blank=True, null=True, verbose_name='Data e Hora da Resolução')

    @property
    def is_open(self):
        return self.status == Status.PENDING and timezone.now() - self.created_at <= self.EXPIRY

    @property
    def state(self):
        if self.status == Status.PENDING and not self.is_open:
            return 'expired'
        return 'open' if self.is_open else self.status

    def __str__(self):
        return f'{self.get_action_display()} {self.get_kind_display()} ({self.get_status_display()})'

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=Kind.values),
                name='proposal_kind_within_choices',
                violation_error_message='O registro precisa ser uma das opções previstas.',
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=Action.values),
                name='proposal_action_within_choices',
                violation_error_message='A ação precisa ser uma das opções previstas.',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=Status.values),
                name='proposal_status_within_choices',
                violation_error_message='A situação precisa ser uma das opções previstas.',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(action=Action.CREATE, target_id__isnull=True)
                    | (~models.Q(action=Action.CREATE) & models.Q(target_id__isnull=False))
                ),
                name='proposal_target_only_outside_create',
                violation_error_message='Editar e apagar apontam para um registro; criar, não.',
            ),
        ]
        verbose_name = 'Proposta'
        verbose_name_plural = 'Propostas'


# O que o usuário escreve custa prompt, e não espaço no banco: a mensagem vai
# inteira a cada rodada, e as instruções de um comando a cada chamada, somadas
# ao que vier depois do nome. Folgado para uma pergunta, estreito para um
# arquivo colado.
MAX_MESSAGE = 2000


class Command(models.Model):
    # Quantos comandos cabem para quem usa o assistente sem permissão de faixa.
    LIMIT = 5

    # Da maior para a menor: quem tem mais de uma fica com a maior. None é sem
    # teto, e o superusuário cai nele porque tem todas as permissões.
    TIERS = [
        ('assistant.unlimited_commands', None),
        ('assistant.command_limit_20', 20),
        ('assistant.command_limit_10', 10),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assistant_commands', verbose_name='Usuário')
    name = models.CharField(max_length=40, verbose_name='Nome')
    instructions = models.TextField(max_length=MAX_MESSAGE, verbose_name='Instruções')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Data e Hora da Atualização')

    # Perder a faixa não apaga nada: quem fica acima do teto segue chamando e
    # editando o que tem, e só não cria outro.
    @classmethod
    def limit_for(cls, user):
        for permission, limit in cls.TIERS:
            if user.has_perm(permission):
                return limit
        return cls.LIMIT

    def __str__(self):
        return f'/{self.name}'

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'name'],
                name='command_unique_user_name',
                violation_error_message='Você já tem um comando com esse nome.',
            ),
            # O nome é o que se digita depois da barra, então só leva o que
            # cabe numa palavra sem espaço nem acento: /saldo-investido.
            models.CheckConstraint(
                condition=models.Q(name__regex=r'^[a-z0-9]+(-[a-z0-9]+)*$'),
                name='command_name_is_slug',
                violation_error_message='O nome precisa ter letras ou números, separados só por hífen.',
            ),
        ]
        permissions = [
            ('command_limit_10', 'Can have up to 10 commands'),
            ('command_limit_20', 'Can have up to 20 commands'),
            ('unlimited_commands', 'Can have unlimited commands'),
        ]
        verbose_name = 'Comando'
        verbose_name_plural = 'Comandos'
