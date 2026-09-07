from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from .account import Account
from .business_rule import BusinessRule
from .choices import Method, Type


class Card(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cards', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='cards', verbose_name='Conta')
    last_digits = models.CharField(max_length=4, validators=[RegexValidator(r'^\d{4}$', 'Informe exatamente os quatro últimos dígitos.')], verbose_name='Últimos Quatro Dígitos')
    closing_day = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(31)], verbose_name='Dia de Fechamento')
    due_day = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(31)], verbose_name='Dia de Vencimento')

    TYPE = Type.OUT
    METHOD = Method.CREDIT

    def clean(self):
        super().clean()
        if self.account_id and not BusinessRule.objects.filter(account=self.account, type=self.TYPE, method=self.METHOD).exists():
            raise ValidationError({'account': f'A conta não permite {Type(self.TYPE).label.lower()} em {Method(self.METHOD).label}, necessário para registrar as compras do cartão.'})
        if self.closing_day and self.due_day and self.closing_day > self.due_day:
            raise ValidationError('A data de fechamento não pode ser posterior a data de vencimento.')

    def __str__(self):
        return f'{self.account} (final {self.last_digits})'

    class Meta:
        ordering = ['account__description', 'last_digits']
        unique_together = ('user', 'account', 'last_digits')
        verbose_name = 'Cartão'
        verbose_name_plural = 'Cartões'
