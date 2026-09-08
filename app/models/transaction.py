from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from ..utils.formatting import format_to_money
from .account import Account
from .business_rule import BusinessRule
from .card import Card
from .category import Category
from .choices import Method, Nature, Type


class Transaction(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='transactions', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='transactions', verbose_name='Conta')
    card = models.ForeignKey(Card, on_delete=models.RESTRICT, blank=True, null=True, related_name='transactions', verbose_name='Cartão')
    type = models.CharField(max_length=20, choices=Type.choices, verbose_name='Tipo')
    method = models.CharField(max_length=20, choices=Method.choices, verbose_name='Método')
    nature = models.CharField(max_length=20, choices=Nature.choices, default=Nature.REGULAR, verbose_name='Natureza')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, related_name='transactions', verbose_name='Categoria')
    description = models.CharField(max_length=200, blank=True, verbose_name='Descrição')
    value = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))], verbose_name='Valor')
    occurred_at = models.DateField(verbose_name='Data da Transação')
    effective_at = models.DateField(editable=False, verbose_name='Data Efetiva')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Data e Hora da Atualização')

    installment = models.ForeignKey('app.Installment', on_delete=models.CASCADE, blank=True, null=True, editable=False, related_name='transactions', verbose_name='Parcelamento')
    parcel = models.PositiveSmallIntegerField(blank=True, null=True, editable=False, verbose_name='Parcela')
    transfer = models.ForeignKey('app.Transfer', on_delete=models.CASCADE, blank=True, null=True, editable=False, related_name='transactions', verbose_name='Transferência')

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
                raise ValidationError({'card': f'O cartão só se aplica a transações em {Method.CREDIT.label}.'})
        elif self.method == Method.CREDIT:
            raise ValidationError({'card': f'Transações em {Method.CREDIT.label} exigem que seja informado um cartão.'})
        if self.category_id and self.nature != Nature.REGULAR:
            raise ValidationError({'category': f'Transações com natureza {Nature(self.nature).label} não recebem categoria.'})

    def calculate_effective_at(self):
        if self.method == Method.CREDIT and self.card_id:
            return self.card.charge_date(self.occurred_at)
        return self.occurred_at

    def save(self, *args, **kwargs):
        self.effective_at = self.calculate_effective_at()
        super().save(*args, **kwargs)

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    def __str__(self):
        return f'{self.category_display} ({format_to_money(self.value)})'

    class Meta:
        ordering = ['-effective_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(value__gte=Decimal('0.01')),
                name='transaction_value_positive',
                violation_error_message='O valor deve ser maior que zero.',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(method=Method.CREDIT, card__isnull=False)
                    | (~models.Q(method=Method.CREDIT) & models.Q(card__isnull=True))
                ),
                name='transaction_card_only_on_credit',
                violation_error_message=f'Transações em {Method.CREDIT.label} exigem um cartão, e o cartão só se aplica a esse método.',
            ),
            models.CheckConstraint(
                condition=models.Q(nature=Nature.REGULAR) | models.Q(category__isnull=True),
                name='transaction_category_only_when_regular',
                violation_error_message=f'Apenas transações com natureza {Nature.REGULAR.label} recebem categoria.',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(installment__isnull=False, parcel__isnull=False)
                    | models.Q(installment__isnull=True, parcel__isnull=True)
                ),
                name='transaction_parcel_only_within_installment',
                violation_error_message='O número da parcela só existe em transações de um parcelamento.',
            ),
            models.UniqueConstraint(
                fields=['installment', 'parcel'],
                name='transaction_unique_installment_parcel',
                violation_error_message='O parcelamento já tem uma transação com esse número de parcela.',
            ),
            models.UniqueConstraint(
                fields=['transfer', 'type'],
                name='transaction_unique_transfer_leg',
                violation_error_message='A transferência já tem uma transação desse tipo.',
            ),
            models.CheckConstraint(
                condition=~models.Q(installment__isnull=False, transfer__isnull=False),
                name='transaction_single_origin',
                violation_error_message='Uma transação vem de um parcelamento ou de uma transferência, nunca dos dois.',
            ),
        ]
        indexes = [
            models.Index(fields=['user', '-effective_at'], name='transaction_effective_at_idx'),
        ]
        verbose_name = 'Transação'
        verbose_name_plural = 'Transações'
