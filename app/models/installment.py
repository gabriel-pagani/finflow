from decimal import Decimal, ROUND_DOWN

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import models, transaction

from ..utils.dates import add_months
from ..utils.formatting import format_to_money
from .account import Account
from .business_rule import BusinessRule
from .card import Card
from .category import Category
from .choices import Method, Nature, Type
from .transaction import Transaction


class Installment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='installments', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='installments', verbose_name='Conta')
    card = models.ForeignKey(Card, on_delete=models.RESTRICT, related_name='installments', verbose_name='Cartão')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, related_name='installments', verbose_name='Categoria')
    description = models.CharField(max_length=200, blank=True, verbose_name='Descrição')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor Total')
    installments = models.PositiveSmallIntegerField(verbose_name='Número de Parcelas')
    occurred_at = models.DateField(verbose_name='Data da Compra')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Data e Hora da Atualização')

    TYPE = Type.OUT
    METHOD = Method.CREDIT
    NATURE = Nature.REGULAR

    def clean(self):
        super().clean()
        errors = {}

        if self.account_id and not BusinessRule.objects.filter(account_id=self.account_id, type=self.TYPE, method=self.METHOD).exists():
            errors['account'] = f'A conta não permite {self.TYPE.label.lower()} em {self.METHOD.label}, necessário para registrar as parcelas.'

        if self.card_id:
            card = []
            if self.account_id and self.card.account_id != self.account_id:
                card.append('O cartão escolhido pertence a outra conta.')
            if self.user_id and self.card.user_id != self.user_id:
                card.append('O cartão escolhido pertence a outro usuário.')
            if card:
                errors['card'] = card

        if errors:
            raise ValidationError(errors)

    def parcel_values(self):
        """Divide o total em parcelas iguais, com a sobra dos centavos na última."""
        base_value = (self.value / self.installments).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        last_value = self.value - base_value * (self.installments - 1)

        return [base_value] * (self.installments - 1) + [last_value]

    def generate_transactions(self):
        self.transactions.all().delete()

        parcels = []
        for number, parcel_value in enumerate(self.parcel_values(), start=1):
            parcel = Transaction(
                user=self.user,
                account=self.account,
                card=self.card,
                type=self.TYPE,
                method=self.METHOD,
                nature=self.NATURE,
                category=self.category,
                description=self.description,
                value=parcel_value,
                occurred_at=add_months(self.occurred_at, number - 1),
                installment=self,
                parcel=number,
            )
            parcel.effective_at = parcel.calculate_effective_at()
            parcels.append(parcel)

        Transaction.objects.bulk_create(parcels)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.generate_transactions()

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    def __str__(self):
        return f'{format_to_money(self.value)} ({self.installments}x)'

    class Meta:
        ordering = ['-occurred_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(installments__range=(2, 360)),
                name='installment_parcels_within_range',
                violation_error_message='Um parcelamento deve ter de 2 a 360 parcelas.',
            ),
            models.CheckConstraint(
                condition=models.Q(value__gte=models.F('installments') * Decimal('0.01')),
                name='installment_value_covers_parcels',
                violation_error_message='O valor deve dar ao menos um centavo para cada parcela.',
            ),
        ]
        verbose_name = 'Parcelamento'
        verbose_name_plural = 'Parcelamentos'
