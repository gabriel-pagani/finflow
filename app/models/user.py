from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    email = models.EmailField(blank=True, null=True, verbose_name='Endereço de email')
    observations = models.TextField(blank=True, default='', verbose_name='Observações')

    def clean(self):
        super().clean()
        self.email = self.email or None
        if self.email and User.objects.filter(email__iexact=self.email).exclude(pk=self.pk).exists():
            raise ValidationError({'email': 'Já existe um usuário com este e-mail.'})

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower('email'),
                condition=models.Q(email__isnull=False) & ~models.Q(email=''),
                name='user_unique_email_case_insensitive',
                violation_error_message='Já existe um usuário com este e-mail.',
            ),
        ]
