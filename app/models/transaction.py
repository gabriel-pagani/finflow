from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from .account import Account
from .business_rule import BusinessRule
from .card import Card
from .category import Category
from .choices import Method, Nature, Type


class Transaction(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='transactions', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='transactions', verbose_name='Conta')
    card = models.ForeignKey(Card, on_delete=models.PROTECT, blank=True, null=True, related_name='transactions', verbose_name='Cartão')
    type = models.CharField(max_length=20, choices=Type.choices, verbose_name='Tipo')
    method = models.CharField(max_length=20, choices=Method.choices, verbose_name='Método')
    nature = models.CharField(max_length=20, choices=Nature.choices, default=Nature.REGULAR, verbose_name='Natureza')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, related_name='transactions', verbose_name='Categoria')
    description = models.CharField(max_length=200, blank=True, verbose_name='Descrição')
    value = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))], verbose_name='Valor')
    occurred_at = models.DateTimeField(verbose_name='Data e Hora da Transação')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Data e Hora da Atualização')

    def clean(self):
        super().clean()
        if self.account_id and self.type and self.method:
            if not BusinessRule.objects.filter(account=self.account, type=self.type, method=self.method).exists():
                raise ValidationError('Combinação de conta, tipo e método não permitida pelas regras de negócio.')
        if self.card_id:
            if self.account_id and self.card.account_id != self.account_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outra conta.'})
            if self.user_id and self.card.user_id != self.user_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outro usuário.'})
            if self.method and self.method != Method.CREDIT:
                raise ValidationError({'card': f'O cartão só se aplica a lançamentos em {Method.CREDIT.label}.'})
        if self.category_id and self.nature != Nature.REGULAR:
            raise ValidationError({'category': f'Lançamentos com natureza {Nature(self.nature).label} não recebem categoria.'})

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    def __str__(self):
        return f'{self.category_display} ({self.value})'

    class Meta:
        ordering = ['-occurred_at']
        indexes = [
            models.Index(fields=['user', '-occurred_at'], name='transaction_user_date_idx'),
        ]
        verbose_name = 'Transação'
        verbose_name_plural = 'Transações'
