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
        unique_together = ('account', 'type', 'method')
        verbose_name = 'Regra de Negócio'
        verbose_name_plural = 'Regras de Negócio'
