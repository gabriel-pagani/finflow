from django.db import models
from django.db.models.functions import Lower


class Account(models.Model):
    description = models.CharField(max_length=100, verbose_name='Conta')

    def __str__(self):
        return self.description

    class Meta:
        ordering = ['description']
        constraints = [
            models.UniqueConstraint(
                Lower('description'),
                name='account_unique_description',
                violation_error_message='Já existe uma conta com essa descrição.',
            ),
            models.CheckConstraint(
                condition=models.Q(description__regex=r'^\S(.*\S)?$'),
                name='account_description_not_blank',
                violation_error_message='A descrição não pode ficar em branco nem começar ou terminar com espaço.',
            ),
        ]
        verbose_name = 'Conta'
        verbose_name_plural = 'Contas'
