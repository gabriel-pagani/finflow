from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from ..utils.formatting import format_to_money
from .account import Account
from .business_rule import BusinessRule
from .choices import Method, Nature, Type
from .transaction import Transaction


class Transfer(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='transfers', verbose_name='Usuário')
    origin = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='transfers_out', verbose_name='Conta de Origem')
    destination = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='transfers_in', verbose_name='Conta de Destino')
    description = models.CharField(max_length=200, blank=True, verbose_name='Descrição')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor')
    occurred_at = models.DateField(verbose_name='Data da Transferência')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Data e Hora da Atualização')

    ORIGIN_TYPE = Type.OUT
    ORIGIN_METHOD = Method.DEBIT

    DESTINATION_TYPE = Type.IN
    DESTINATION_METHOD = Method.NOT_APPLICABLE

    NATURE = Nature.INTERNAL

    def clean(self):
        super().clean()
        errors = {}

        if self.origin_id and not BusinessRule.objects.filter(account_id=self.origin_id, type=self.ORIGIN_TYPE, method=self.ORIGIN_METHOD).exists():
            errors['origin'] = f'A conta de origem não permite {self.ORIGIN_TYPE.label.lower()} em {self.ORIGIN_METHOD.label}, necessário para registrar a transferência.'

        if self.destination_id and not BusinessRule.objects.filter(account_id=self.destination_id, type=self.DESTINATION_TYPE, method=self.DESTINATION_METHOD).exists():
            errors['destination'] = f'A conta de destino não permite {self.DESTINATION_TYPE.label.lower()} em {self.DESTINATION_METHOD.label}, necessário para registrar a transferência.'

        if errors:
            raise ValidationError(errors)

    def generate_transactions(self):
        self.transactions.all().delete()

        common = {
            'user': self.user,
            'nature': self.NATURE,
            'description': self.description,
            'value': self.value,
            'occurred_at': self.occurred_at,
            'transfer': self,
        }
        legs = [
            Transaction(account=self.origin, type=self.ORIGIN_TYPE, method=self.ORIGIN_METHOD, **common),
            Transaction(account=self.destination, type=self.DESTINATION_TYPE, method=self.DESTINATION_METHOD, **common),
        ]
        for leg in legs:
            leg.effective_at = leg.calculate_effective_at()

        Transaction.objects.bulk_create(legs)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.generate_transactions()

    def __str__(self):
        return f'{format_to_money(self.value)} ({self.origin} → {self.destination})'

    class Meta:
        ordering = ['-occurred_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(value__gte=Decimal('0.01')),
                name='transfer_value_positive',
                violation_error_message='O valor deve ser maior que zero.',
            ),
            models.CheckConstraint(
                condition=~models.Q(origin=models.F('destination')),
                name='transfer_accounts_differ',
                violation_error_message='A conta de destino deve ser diferente da conta de origem.',
            ),
        ]
        verbose_name = 'Transferência'
        verbose_name_plural = 'Transferências'
