from django.db import models
from .account import Account
from .choices import Type, Method


class BusinessRule(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, verbose_name='Conta')
    type = models.CharField(max_length=20, choices=Type.choices, verbose_name='Tipo')
    method = models.CharField(max_length=20, choices=Method.choices, verbose_name='Método')

    def __str__(self):
        return f'{self.account} / {self.get_type_display()} / {self.get_method_display()}'

    class Meta:
        ordering = ['account__description', 'type', 'method']
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'type', 'method'],
                name='business_rule_unique_combination',
                violation_error_message='Essa conta já tem uma regra com esse tipo e método.',
            ),
            models.CheckConstraint(
                condition=models.Q(type__in=Type.values),
                name='business_rule_type_within_choices',
                violation_error_message='O tipo precisa ser uma das opções previstas.',
            ),
            models.CheckConstraint(
                condition=models.Q(method__in=Method.values),
                name='business_rule_method_within_choices',
                violation_error_message='O método precisa ser uma das opções previstas.',
            ),
        ]
        verbose_name = 'Regra de Negócio'
        verbose_name_plural = 'Regras de Negócio'
