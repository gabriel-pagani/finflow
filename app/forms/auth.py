"""Formulários de senha e confirmação por aplicativo."""

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django_otp import match_token

from ..utils import throttle


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
