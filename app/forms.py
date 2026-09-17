from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.db import transaction as db
from django.db.models import Q
from django.forms.utils import ErrorDict
from django.utils import timezone
from django_otp import match_token

from .models import AccessRequest, Card, Installment, Method, Transaction, Transfer, User
from .utils import throttle


CARD_REQUIRED_ERROR = 'Escolha o cartão usado na compra. Se você ainda não tem nenhum, cadastre um em Cartões.'

INVALID_LOGIN_ERROR = 'Usuário e/ou senha inválidos!'

LOGIN_THROTTLED_ERROR = (
    'Muitas tentativas de login nesta conta. Espere uma hora e tente de novo.'
)

TOKEN_INVALID_ERROR = (
    'Código inválido. Confira se digitou o código que está no aplicativo agora; se ele acabou de virar, '
    'espere o próximo e tente de novo.'
)


def token_field(label):
    return forms.CharField(
        label=label,
        max_length=8,
        widget=forms.TextInput(attrs={
            'inputmode': 'numeric', 'autocomplete': 'one-time-code',
            'placeholder': '000000', 'spellcheck': 'false',
        }),
    )


class LoginForm(AuthenticationForm):
    """A primeira etapa: usuário e senha, sem falar em código."""

    error_messages = {
        **AuthenticationForm.error_messages,
        # O mesmo recado para usuário inexistente e senha errada: qual dos dois
        # falhou não é assunto de quem está tentando entrar.
        'invalid_login': INVALID_LOGIN_ERROR,
    }

    # O backend recusa o palpite de quem estourou o teto, mas recusa como senha
    # errada. O recado de que é o teto existe só aqui, na tela de quem usa o
    # sistema: quem espera uma hora precisa saber o motivo, e saber que o teto
    # estourou não conta nada sobre a conta que já não se soubesse.
    def clean(self):
        username = self.cleaned_data.get('username')
        if username and throttle.login_blocked(username):
            raise ValidationError(LOGIN_THROTTLED_ERROR)

        return super().clean()


class LoginTokenForm(forms.Form):
    """
    A segunda etapa, que só existe para quem cadastrou o aplicativo.

    Quem chega aqui já passou pela senha, e a view sabe de quem é a vez: por
    isso o campo aparece para quem tem o que digitar, e só para ele.
    """

    token = token_field('Código de verificação')

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # O dispositivo que conferiu o código, para a view marcar a sessão.
        self.device = None
        self.fields['token'].widget.attrs['autofocus'] = True

    def clean_token(self):
        token = self.cleaned_data['token'].strip()

        # O match_token confere o código em todos os dispositivos do usuário e
        # segura a repetição: cada erro dobra a espera do próximo palpite.
        self.device = match_token(self.user, token)
        if self.device is None:
            raise ValidationError(TOKEN_INVALID_ERROR)
        return token


class OtpSetupForm(forms.Form):
    """O código que confirma que o aplicativo foi cadastrado direito."""

    token = token_field('Código do aplicativo')

    def __init__(self, *args, device=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.device = device
        # É o único campo da tela; no login o foco é do usuário.
        self.fields['token'].widget.attrs['autofocus'] = True

    def clean_token(self):
        token = self.cleaned_data['token'].strip()
        if not self.device.verify_token(token):
            raise ValidationError(TOKEN_INVALID_ERROR)
        return token

ACCESS_REQUEST_NOTE = 'Acesso solicitado pela tela de login em {date:%d/%m/%Y}.'

ACCESS_REQUEST_THROTTLED = (
    'Você já solicitou a criação de uma conta nos últimos sete dias. Aguarde algum administrador aprová-la.'
)

# Tentativas, e não pedidos: a recusa também conta. A recusa não grava pedido, e
# contando só os aceitos quem erra o formulário de propósito insistiria a noite
# toda de graça, cada insistência custando uma consulta ao banco.
ACCESS_REQUEST_ATTEMPTS = 'access-request:attempts:{}'
ACCESS_REQUEST_ATTEMPTS_LIMIT = 10
ACCESS_REQUEST_ATTEMPTS_WINDOW = 60 * 60 * 24

ACCESS_REQUEST_TOO_MANY_ATTEMPTS = (
    'Muitas tentativas de solicitação a partir desta rede. Tente de novo amanhã.'
)


def registered(username, email):
    """Diz se o usuário ou o e-mail já são de alguém, como o cadastro recusaria."""
    if not username:
        return False

    # Sem olhar a caixa nos dois: o cadastro do Django recusa usuário que só
    # difere de maiúscula, e o e-mail tem restrição própria no banco.
    taken = Q(username__iexact=username)
    if email:
        taken |= Q(email__iexact=email)

    return User.objects.filter(taken).exists()


def count_attempt(ip):
    """Soma a tentativa ao IP e diz se ela ainda cabe no teto da janela."""
    attempts = throttle.count(ACCESS_REQUEST_ATTEMPTS.format(ip), ACCESS_REQUEST_ATTEMPTS_WINDOW)
    return attempts <= ACCESS_REQUEST_ATTEMPTS_LIMIT


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
        # Quem já tem conta, sabido no clean e calado na tela.
        self.taken = False

        self.fields['first_name'].required = True
        self.fields['email'].required = True

        for field in self.fields.values():
            field.help_text = ''

    # Antes de qualquer campo: quem passou do teto não pergunta mais nada ao
    # banco, e é a validação dos campos que pergunta.
    def full_clean(self):
        if self.is_bound and self.ip and not count_attempt(self.ip):
            self._errors = ErrorDict()
            self.cleaned_data = {}
            self.add_error(None, ACCESS_REQUEST_TOO_MANY_ATTEMPTS)
            return

        super().full_clean()

    # O erro de usuário repetido nasceria aqui, contando que a conta existe.
    # Quem já tem conta é anotado no clean e sai desta tela com a mesma resposta
    # de quem é novo: de fora, não há como separar um caso do outro.
    def clean_username(self):
        return self.cleaned_data['username']

    def clean(self):
        if self.ip and AccessRequest.recent(self.ip):
            raise ValidationError(ACCESS_REQUEST_THROTTLED)

        cleaned = super().clean()
        self.taken = registered(cleaned.get('username'), cleaned.get('email'))

        return cleaned

    # As outras duas validações que apontariam o campo repetido. Quem decide se
    # o cadastro existe é o registered, calado; o banco segue com as restrições
    # dele, que é o que impede dois cadastros iguais de verdade.
    #
    # O resto da validação não pode ser pulado junto: se a senha curta deixasse
    # de reclamar quando a conta existe, a diferença entre as duas telas diria
    # exatamente o que o recado dizia.
    def validate_unique(self):
        pass

    def validate_constraints(self):
        pass

    def save(self, commit=True):
        # Quem já tem conta não cria cadastro nenhum: sobrescrever seria
        # entregar a conta de alguém a quem pediu. O pedido, sim, é gravado, sem
        # usuário — ele é o que segura o IP pela semana, e é por ele que a
        # insistência daqui custa o mesmo que a de um pedido de verdade.
        if self.taken:
            if commit and self.ip:
                AccessRequest.objects.create(ip=self.ip)
            return None

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

    def clean(self):
        cleaned = super().clean()

        if cleaned.get('method') != Method.CREDIT:
            cleaned['card'] = None
        elif not cleaned.get('card') and 'card' not in self.errors:
            self.add_error('card', CARD_REQUIRED_ERROR)

        return cleaned
