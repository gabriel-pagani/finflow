from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
import pytest

from app.models import (
    Account,
    BusinessRule,
    Category,
    Contribution,
    Installment,
    Investment,
    Method,
    Transaction,
    Transfer,
    Type,
)


@pytest.fixture(autouse=True)
def disable_axes(settings):
    """Desliga o Axes nos testes.

    O bloqueio conta falhas por usuário e IP, e o client de teste usa sempre o
    mesmo IP: a partir da quinta tentativa de login os casos seguintes falhariam
    por bloqueio, não pelo que cada um se propõe a verificar.
    """
    settings.AXES_ENABLED = False


@pytest.fixture(autouse=True)
def no_ssl_redirect(settings):
    """Desliga o redirect para HTTPS durante os testes.

    O CI roda com DEBUG=0, que liga o SECURE_SSL_REDIRECT: sem isto o client de
    teste receberia 301 para https em toda requisição e nenhuma chegaria à view.
    O redirect em si é comportamento de produção, verificado no deploy.
    """
    settings.SECURE_SSL_REDIRECT = False


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Troca o storage de estáticos por um sem manifesto.

    O ManifestStaticFilesStorage resolve cada {% static %} pelo manifesto que o
    collectstatic gera, e sem ele qualquer template que renderize um estático
    estoura. Rodar o collectstatic antes da suíte só para isso deixaria os
    testes mais lentos e presos a um passo externo.
    """
    settings.STORAGES = {
        **settings.STORAGES,
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }


# --------------------------------------------------------------------------
# Cadastros compartilhados
#
# Conta, categoria e regra de negócio não têm dono: são tabelas globais, usadas
# por todos os usuários. É justamente por isso que elas aparecem nos testes de
# isolamento — o recorte por usuário tem de vir do filtro da view, não do fato
# de cada um enxergar cadastros diferentes.
# --------------------------------------------------------------------------

@pytest.fixture
def account(db):
    return Account.objects.create(description='Conta Corrente')


@pytest.fixture
def destination_account(db):
    """Segunda conta, necessária para a transferência ter para onde ir."""
    return Account.objects.create(description='Poupanca')


@pytest.fixture
def category(db):
    return Category.objects.create(description='Alimentacao')


@pytest.fixture
def business_rules(account, destination_account):
    """Libera nas duas contas todas as combinações que os factories usam.

    Transaction.clean() recusa conta/tipo/método sem regra cadastrada. Aqui as
    regras são abertas de propósito: estes testes verificam quem alcança o quê,
    e uma combinação barrada por falta de regra faria um caso falhar por motivo
    que não é o dele.
    """
    combinations = [
        (Type.OUT, Method.DEBIT),
        (Type.IN, Method.DEBIT),
        (Type.OUT, Method.CREDIT),
        (Type.IN, Method.CREDIT),
        (Type.OUT, Method.NOT_APPLICABLE),
        (Type.IN, Method.NOT_APPLICABLE),
    ]

    return BusinessRule.objects.bulk_create([
        BusinessRule(account=account_obj, type=type_, method=method)
        for account_obj in (account, destination_account)
        for type_, method in combinations
    ])


# --------------------------------------------------------------------------
# Usuários
# --------------------------------------------------------------------------

ALICE_PASSWORD = 'senha-de-teste-alice'
BOB_PASSWORD = 'senha-de-teste-bob'


@pytest.fixture
def alice(db):
    return get_user_model().objects.create_user(username='alice', password=ALICE_PASSWORD)


@pytest.fixture
def bob(db):
    return get_user_model().objects.create_user(username='bob', password=BOB_PASSWORD)


@pytest.fixture
def alice_logged(client, alice):
    assert client.login(username=alice.username, password=ALICE_PASSWORD)
    return client


@pytest.fixture
def bob_logged(client, bob):
    assert client.login(username=bob.username, password=BOB_PASSWORD)
    return client


# --------------------------------------------------------------------------
# Factories dos registros com dono
#
# Todas recebem o usuário como primeiro argumento: é isso que deixa cada teste
# dizer, na própria chamada, de quem é o dado que ele está criando.
# --------------------------------------------------------------------------

@pytest.fixture
def make_transaction(account, category, business_rules):
    """Cria uma transação avulsa para o usuário informado."""
    def _make(user, **kwargs):
        fields = {
            'user': user,
            'account': account,
            'category': category,
            'type': Type.OUT,
            'method': Method.DEBIT,
            'description': 'Almoco',
            'value': '25.00',
            'datetime': timezone.now(),
        }
        fields.update(kwargs)
        return Transaction.objects.create(**fields)
    return _make


@pytest.fixture
def make_installment(account, category, business_rules):
    """Cria um parcelamento, que no save gera uma transação por parcela."""
    def _make(user, **kwargs):
        fields = {
            'user': user,
            'account': account,
            'category': category,
            'description': 'Notebook',
            # Decimal e não string: o save() divide o total entre as parcelas
            # antes de o Django converter o que viesse como texto.
            'value': Decimal('1200.00'),
            'installments': 3,
            'datetime': timezone.now() + timedelta(days=1),
        }
        fields.update(kwargs)
        return Installment.objects.create(**fields)
    return _make


@pytest.fixture
def make_transfer(account, destination_account, category, business_rules):
    """Cria uma transferência, que no save gera o par saída/entrada."""
    def _make(user, **kwargs):
        fields = {
            'user': user,
            'origin': account,
            'destination': destination_account,
            'category': category,
            'description': 'Reserva',
            'value': Decimal('300.00'),
            'datetime': timezone.now(),
        }
        fields.update(kwargs)
        return Transfer.objects.create(**fields)
    return _make


@pytest.fixture
def make_investment(account, category, business_rules):
    """Cria um investimento com uma aplicação, que gera a transação da saída."""
    def _make(user, value=Decimal('1000.00'), **kwargs):
        fields = {
            'user': user,
            'account': account,
            'category': category,
            'description': 'Tesouro Selic',
        }
        fields.update(kwargs)
        investment = Investment.objects.create(**fields)

        Contribution.objects.create(
            investment=investment,
            value=value,
            datetime=timezone.now(),
        )

        return investment
    return _make


@pytest.fixture
def derived_transaction(make_installment, alice):
    """Parcela da Alice: transação que existe por causa de um registro de origem."""
    return make_installment(alice).transactions.first()
