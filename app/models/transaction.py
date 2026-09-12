from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
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
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor')
    occurred_at = models.DateField(verbose_name='Data da Transação')
    effective_at = models.DateField(editable=False, verbose_name='Data Efetiva')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Data e Hora da Atualização')

    installment = models.ForeignKey('app.Installment', on_delete=models.CASCADE, blank=True, null=True, editable=False, related_name='transactions', verbose_name='Parcelamento')
    parcel = models.PositiveSmallIntegerField(blank=True, null=True, editable=False, verbose_name='Parcela')
    transfer = models.ForeignKey('app.Transfer', on_delete=models.CASCADE, blank=True, null=True, editable=False, related_name='transactions', verbose_name='Transferência')

    def clean(self):
        super().clean()
        if self.nature == Nature.INTERNAL and not self.transfer_id:
            raise ValidationError({'nature': f'A natureza {Nature.INTERNAL.label} é exclusiva das pernas de uma transferência.'})
        if self.account_id and self.type and self.method:
            if not BusinessRule.objects.filter(account_id=self.account_id, type=self.type, method=self.method).exists():
                raise ValidationError('Combinação de conta, tipo e método não permitida pelas regras de negócio.')
        if self.card_id:
            if self.account_id and self.card.account_id != self.account_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outra conta.'})
            if self.user_id and self.card.user_id != self.user_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outro usuário.'})

    def calculate_effective_at(self):
        if self.method == Method.CREDIT and self.card_id:
            return self.card.charge_date(self.occurred_at)
        return self.occurred_at

    def save(self, *args, **kwargs):
        self.effective_at = self.calculate_effective_at()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_derived:
            raise ValidationError('Esta transação é derivada e só pode ser apagada junto do parcelamento ou da transferência que a criou.')
        return super().delete(*args, **kwargs)

    @property
    def is_derived(self):
        return bool(self.installment_id or self.transfer_id)

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
                condition=models.Q(type__in=Type.values),
                name='transaction_type_within_choices',
                violation_error_message='O tipo precisa ser uma das opções previstas.',
            ),
            models.CheckConstraint(
                condition=models.Q(method__in=Method.values),
                name='transaction_method_within_choices',
                violation_error_message='O método precisa ser uma das opções previstas.',
            ),
            models.CheckConstraint(
                condition=models.Q(nature__in=Nature.values),
                name='transaction_nature_within_choices',
                violation_error_message='A natureza precisa ser uma das opções previstas.',
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
                condition=~models.Q(nature=Nature.ADJUSTMENT) | models.Q(method=Method.NOT_APPLICABLE),
                name='transaction_adjustment_without_method',
                violation_error_message=f'Um ajuste de saldo não tem método, use {Method.NOT_APPLICABLE.label}.',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(installment__isnull=False, parcel__isnull=False)
                    | models.Q(installment__isnull=True, parcel__isnull=True)
                ),
                name='transaction_parcel_only_within_installment',
                violation_error_message='O número da parcela só existe em transações de um parcelamento.',
            ),
            models.CheckConstraint(
                condition=models.Q(parcel__isnull=True) | models.Q(parcel__gte=1),
                name='transaction_parcel_positive',
                violation_error_message='O número da parcela começa em 1.',
            ),
            models.CheckConstraint(
                condition=models.Q(effective_at__gte=models.F('occurred_at')),
                name='transaction_effective_after_occurrence',
                violation_error_message='A data efetiva não pode ser anterior à data da transação.',
            ),
            models.CheckConstraint(
                condition=models.Q(method=Method.CREDIT) | models.Q(effective_at=models.F('occurred_at')),
                name='transaction_effective_equals_occurrence_outside_credit',
                violation_error_message=f'Fora do {Method.CREDIT.label}, a data efetiva é a própria data da transação.',
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
            models.CheckConstraint(
                condition=models.Q(installment__isnull=True) | models.Q(nature=Nature.REGULAR),
                name='transaction_parcel_is_regular',
                violation_error_message=f'A parcela de um parcelamento é sempre de natureza {Nature.REGULAR.label}.',
            ),
            models.CheckConstraint(
                condition=models.Q(installment__isnull=True) | models.Q(type=Type.OUT, method=Method.CREDIT),
                name='transaction_parcel_is_credit_expense',
                violation_error_message=f'A parcela de um parcelamento é sempre {Type.OUT.label.lower()} em {Method.CREDIT.label}.',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(transfer__isnull=False, nature=Nature.INTERNAL)
                    | (models.Q(transfer__isnull=True) & ~models.Q(nature=Nature.INTERNAL))
                ),
                name='transaction_internal_only_within_transfer',
                violation_error_message=f'A natureza {Nature.INTERNAL.label} é exclusiva das pernas de uma transferência.',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(transfer__isnull=True)
                    | models.Q(type=Type.OUT, method=Method.DEBIT)
                    | models.Q(type=Type.IN, method=Method.NOT_APPLICABLE)
                ),
                name='transaction_transfer_leg_methods',
                violation_error_message='A saída da transferência é em débito e a entrada não se aplica.',
            ),
        ]
        indexes = [
            models.Index(fields=['user', '-effective_at'], name='transaction_effective_at_idx'),
        ]
        verbose_name = 'Transação'
        verbose_name_plural = 'Transações'
