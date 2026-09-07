from django.db import models


class Category(models.Model):
    description = models.CharField(max_length=100, unique=True, verbose_name='Categoria')

    def __str__(self):
        return self.description

    class Meta:
        ordering = ['description']
        verbose_name = 'Categoria'
        verbose_name_plural = 'Categorias'
