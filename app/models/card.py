from calendar import monthrange
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .account import Account
from .business_rule import BusinessRule
from .choices import Method, Type


class Card(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cards', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='cards', verbose_name='Conta')
    last_digits = models.CharField(max_length=4, verbose_name='Últimos Quatro Dígitos')
    closing_day = models.PositiveSmallIntegerField(verbose_name='Dia de Fechamento')
    due_day = models.PositiveSmallIntegerField(verbose_name='Dia de Vencimento')

    TYPE = Type.OUT
    METHOD = Method.CREDIT

    LOCKED_AFTER_TRANSACTIONS = ('user', 'account', 'last_digits')

    def clean(self):
        super().clean()
        if self.account_id and not BusinessRule.objects.filter(account_id=self.account_id, type=self.TYPE, method=self.METHOD).exists():
            raise ValidationError({'account': f'A conta não permite {self.TYPE.label.lower()} em {self.METHOD.label}, necessário para registrar as compras do cartão.'})
        if self.pk and self.transactions.exists():
            anterior = Card.objects.get(pk=self.pk)
            if (self.user_id, self.account_id, self.last_digits) != (anterior.user_id, anterior.account_id, anterior.last_digits):
                raise ValidationError('Esse cartão já tem transações registradas, apenas as datas de fechamento e vencimento podem ser alteradas.')

    @staticmethod
    def _next_month(year, month):
        return (year + 1, 1) if month == 12 else (year, month + 1)

    def charge_date(self, occurred_on):
        year, month = occurred_on.year, occurred_on.month
        if occurred_on.day >= min(self.closing_day, monthrange(year, month)[1]):
            year, month = self._next_month(year, month)
        if self.due_day <= self.closing_day:
            year, month = self._next_month(year, month)
        due = date(year, month, min(self.due_day, monthrange(year, month)[1]))
        if due.isoweekday() > 5:
            due += timedelta(days=8 - due.isoweekday())
        return due

    def __str__(self):
        return f'{self.account} (final {self.last_digits})'

    class Meta:
        ordering = ['account__description', 'last_digits']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'account', 'last_digits'],
                name='card_unique_user_account_digits',
                violation_error_message='Esse cartão já foi cadastrado.',
            ),
            models.CheckConstraint(
                condition=models.Q(last_digits__regex=r'^\d{4}$'),
                name='card_last_digits_four_numbers',
                violation_error_message='Informe exatamente os últimos quatro dígitos do cartão.',
            ),
            models.CheckConstraint(
                condition=models.Q(closing_day__range=(1, 31)) & models.Q(due_day__range=(1, 31)),
                name='card_days_within_month',
                violation_error_message='Os dias de fechamento e vencimento devem estar entre 1 e 31.',
            ),
        ]
        verbose_name = 'Cartão'
        verbose_name_plural = 'Cartões'
