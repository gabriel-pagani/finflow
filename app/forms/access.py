"""Pedido de acesso e controle de tentativas."""

from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.db import transaction as db
from django.db.models import Q
from django.forms.utils import ErrorDict
from django.utils import timezone

from ..models import AccessRequest, User
from ..utils import throttle


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
