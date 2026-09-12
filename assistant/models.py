from django.conf import settings
from django.db import models


class Role(models.TextChoices):
    USER = 'user', 'Usuário'
    ASSISTANT = 'assistant', 'Assistente'
    TOOL = 'tool', 'Ferramenta'


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
