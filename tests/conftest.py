from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
import pytest

from app.models import Account, BusinessRule, Category, Installment, Method, Transaction, Type


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


@pytest.fixture
def account(db):
    return Account.objects.create(description='Conta Corrente')


@pytest.fixture
def category(db):
    return Category.objects.create(description='Alimentação')


@pytest.fixture
def business_rule(account):
    # Transaction.clean() recusa a combinação conta/tipo/método que não tenha
    # regra cadastrada, então sem isto nenhuma escrita do formulário passaria.
    return BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.DEBIT)


@pytest.fixture
def alice(db):
    return get_user_model().objects.create_user(username='alice', password='senha-de-teste-alice')


@pytest.fixture
def bob(db):
    return get_user_model().objects.create_user(username='bob', password='senha-de-teste-bob')


@pytest.fixture
def make_transaction(account, category, business_rule):
    """Cria uma transação avulsa para o usuário informado."""
    def _make(user, **kwargs):
        fields = {
            'user': user,
            'account': account,
            'category': category,
            'type': Type.OUT,
            'method': Method.DEBIT,
            'description': 'Almoço',
            'value': '25.00',
            'datetime': timezone.now(),
        }
        fields.update(kwargs)
        return Transaction.objects.create(**fields)
    return _make


@pytest.fixture
def derived_transaction(account, category, alice):
    """Transação gerada por parcelamento, que as telas de escrita recusam."""
    BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.CREDIT)

    # O value vai como Decimal porque o save() já divide o total entre as
    # parcelas, antes de o Django converter o que viesse como string.
    installment = Installment.objects.create(
        user=alice,
        account=account,
        category=category,
        description='Notebook',
        value=Decimal('1200.00'),
        installments=3,
        datetime=timezone.now() + timedelta(days=1),
    )

    return installment.transactions.first()
