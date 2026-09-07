from django.db import models


class Account(models.Model):
    description = models.CharField(max_length=100, unique=True, verbose_name='Conta')

    def __str__(self):
        return self.description

    class Meta:
        ordering = ['description']
        verbose_name = 'Conta'
        verbose_name_plural = 'Contas'
