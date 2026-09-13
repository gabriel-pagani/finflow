from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.db import transaction as db
from django.utils import timezone

from .models import AccessRequest, Card, Installment, Method, Nature, Transaction, Transfer, User


CARD_REQUIRED_ERROR = 'Escolha o cartão usado na compra. Se você ainda não tem nenhum, cadastre um em Cartões.'

ACCESS_REQUEST_NOTE = 'Acesso solicitado pela tela de login em {date:%d/%m/%Y}.'

ACCESS_REQUEST_THROTTLED = (
    'Você já solicitou a criação de uma conta nos últimos sete dias. Aguarde algum administrador aprová-la.'
)


class AccessRequestForm(UserCreationForm):
    """
    Cadastro pedido por quem ainda não tem conta.

    O usuário nasce desligado: quem liga é o administrador, no portal, depois de
    saber de quem é o pedido. Até lá o cadastro existe, mas não entra — a
    autenticação recusa usuário inativo.
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('first_name', 'last_name', 'username', 'email',)

    def __init__(self, *args, ip=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.ip = ip

        self.fields['first_name'].required = True
        self.fields['email'].required = True

        for field in self.fields.values():
            field.help_text = ''

    def clean(self):
        if self.ip and AccessRequest.recent(self.ip):
            raise ValidationError(ACCESS_REQUEST_THROTTLED)

        return super().clean()

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = False
        user.observations = ACCESS_REQUEST_NOTE.format(date=timezone.localdate())

        if commit:
            # O pedido é o que segura o IP: gravar o usuário sem ele abriria a
            # janela de novo no erro seguinte.
            with db.atomic():
                user.save()
                # Sem IP não há o que segurar, e o pedido guardaria um campo
                # vazio; é caso de borda, mas de 500 se passar batido.
                if self.ip:
                    AccessRequest.objects.create(user=user, ip=self.ip)

        return user


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
        self.fields['nature'].choices = [choice for choice in Nature.choices if choice[0] != Nature.INTERNAL]

    def clean(self):
        cleaned = super().clean()

        if cleaned.get('method') != Method.CREDIT:
            cleaned['card'] = None
        elif not cleaned.get('card') and 'card' not in self.errors:
            self.add_error('card', CARD_REQUIRED_ERROR)

        return cleaned
