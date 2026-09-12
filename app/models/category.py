from django.db import models
from django.db.models.functions import Lower


class Category(models.Model):
    description = models.CharField(max_length=100, verbose_name='Categoria')

    def __str__(self):
        return self.description

    class Meta:
        ordering = ['description']
        constraints = [
            models.UniqueConstraint(
                Lower('description'),
                name='category_unique_description',
                violation_error_message='Já existe uma categoria com essa descrição.',
            ),
            models.CheckConstraint(
                condition=models.Q(description__regex=r'^\S(.*\S)?$'),
                name='category_description_not_blank',
                violation_error_message='A descrição não pode ficar em branco nem começar ou terminar com espaço.',
            ),
        ]
        verbose_name = 'Categoria'
        verbose_name_plural = 'Categorias'
