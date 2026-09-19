import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from app.forms.finance import OwnedForm

from .models import Command


class CommandForm(OwnedForm):
    class Meta:
        model = Command
        fields = ('name', 'instructions',)
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'saldo-investido', 'autocomplete': 'off', 'autocapitalize': 'off', 'spellcheck': 'false'}),
            'instructions': forms.Textarea(attrs={'rows': 8, 'placeholder': 'O saldo investido é a soma das transações com a categoria Investimentos.'}),
        }

    # "Saldo Investido" e "/saldo-investido" são o mesmo nome: a barra é de
    # quem chama, e o que se guarda é o que se digita depois dela.
    def clean_name(self):
        name = re.sub(r'[-_]+', '-', slugify(self.cleaned_data['name'])).strip('-')
        if not name:
            raise ValidationError('Use letras ou números no nome.')
        return name

    # Só criar esbarra no teto: quem já está nele continua editando o que tem.
    def clean(self):
        cleaned_data = super().clean()
        limit = Command.limit_for(self.user)
        if self.instance.pk is None and limit is not None and Command.objects.filter(user=self.user).count() >= limit:
            raise ValidationError(f'Você já tem {limit} comandos, o limite. Apague um para criar outro.')
        return cleaned_data
