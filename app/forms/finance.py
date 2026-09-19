"""Formulários financeiros compartilhados pelas telas e pelo assistente."""

from django import forms
from django.utils import timezone

from ..models import Card, Installment, Method, Transaction, Transfer


CARD_REQUIRED_ERROR = 'Escolha o cartão usado na compra. Se você ainda não tem nenhum, cadastre um em Cartões.'


class DateInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format='%Y-%m-%d')


class OwnedForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        if 'category' in self.fields:
            self.fields['category'].empty_label = 'Categoria Não Identificada'

        if 'card' in self.fields:
            card = self.fields['card']
            card.queryset = Card.objects.filter(user=user).select_related('account')
            card.empty_label = None
            card.error_messages['required'] = CARD_REQUIRED_ERROR

        if 'occurred_at' in self.fields:
            occurred_at = self.fields['occurred_at']
            occurred_at.input_formats = ['%Y-%m-%d']
            if not self.instance.pk:
                occurred_at.initial = timezone.localdate()

    def _get_validation_exclusions(self):
        return super()._get_validation_exclusions() - {'user'}

    def _post_clean(self):
        if self.user and not self.instance.user_id:
            self.instance.user = self.user
        super()._post_clean()


class CardForm(OwnedForm):
    class Meta:
        model = Card
        fields = ('account', 'last_digits', 'closing_day', 'due_day',)
        widgets = {
            'last_digits': forms.TextInput(attrs={'inputmode': 'numeric', 'maxlength': '4', 'pattern': r'\d{4}'}),
            'closing_day': forms.NumberInput(attrs={'min': '1', 'max': '31', 'step': '1'}),
            'due_day': forms.NumberInput(attrs={'min': '1', 'max': '31', 'step': '1'}),
        }


class InstallmentForm(OwnedForm):
    class Meta:
        model = Installment
        fields = ('account', 'card', 'value', 'installments', 'category', 'occurred_at', 'description',)
        widgets = {
            'occurred_at': DateInput(),
            'value': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'installments': forms.NumberInput(attrs={'min': '2', 'step': '1'}),
        }


class TransferForm(OwnedForm):
    class Meta:
        model = Transfer
        fields = ('origin', 'destination', 'value', 'occurred_at', 'description',)
        widgets = {
            'occurred_at': DateInput(),
            'value': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
        }


class TransactionForm(OwnedForm):
    class Meta:
        model = Transaction
        fields = ('account', 'card', 'type', 'method', 'nature', 'category', 'value', 'occurred_at', 'description',)
        widgets = {
            'occurred_at': DateInput(),
            'value': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['card'].required = False

    def clean(self):
        cleaned = super().clean()

        if cleaned.get('method') != Method.CREDIT:
            cleaned['card'] = None
        elif not cleaned.get('card') and 'card' not in self.errors:
            self.add_error('card', CARD_REQUIRED_ERROR)

        return cleaned
