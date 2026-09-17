"""
Fixtures compartilhadas pelos testes de model.

A conta padrão tem regra de saída em débito e em crédito, o cartão fecha dia 5 e
vence dia 12 — a mesma configuração usada como referência em
test_card_charge_date.py, para que as datas esperadas coincidam entre os testes.
"""
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from app.models import Account, BusinessRule, Card, Category, Installment, Method, Transaction, Transfer, Type


# Toda tela exige o segundo fator, então a sessão do teste nasce como a de quem
# entrou pelo login com o código na mão. Quem testa o próprio cadastro monta a
# sessão por conta, sem isto.
def sign_in(client, user):
    device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
    client.force_login(user)

    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()

    return device


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username='gabriel', password='segredo')


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(username='mariana', password='segredo')


@pytest.fixture
def account(db):
    return Account.objects.create(description='Nubank')


@pytest.fixture
def other_account(db):
    return Account.objects.create(description='Itaú')


@pytest.fixture
def category(db):
    return Category.objects.create(description='Mercado')


@pytest.fixture
def debit_rule(account):
    return BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.DEBIT)


@pytest.fixture
def credit_rule(account):
    return BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.CREDIT)


@pytest.fixture
def other_debit_rule(other_account):
    """Saída em débito na segunda conta, para os testes que precisam das duas."""
    return BusinessRule.objects.create(account=other_account, type=Type.OUT, method=Method.DEBIT)


@pytest.fixture
def transfer_out_rule(account):
    """Saída em débito na conta de origem."""
    return BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.DEBIT)


@pytest.fixture
def transfer_in_rule(other_account):
    """Entrada em não se aplica na conta de destino."""
    return BusinessRule.objects.create(account=other_account, type=Type.IN, method=Method.NOT_APPLICABLE)


@pytest.fixture
def make_card():
    """Cartão sem banco: charge_date só depende dos dias do ciclo."""
    def build(closing_day, due_day):
        return Card(closing_day=closing_day, due_day=due_day)
    return build


@pytest.fixture
def card(user, account, credit_rule):
    return Card.objects.create(user=user, account=account, last_digits='1234',
                               closing_day=5, due_day=12)


@pytest.fixture
def other_account_card(user, other_account):
    BusinessRule.objects.create(account=other_account, type=Type.OUT, method=Method.CREDIT)
    return Card.objects.create(user=user, account=other_account, last_digits='5678',
                               closing_day=5, due_day=12)


@pytest.fixture
def other_user_card(other_user, account, credit_rule):
    return Card.objects.create(user=other_user, account=account, last_digits='5678',
                               closing_day=5, due_day=12)


@pytest.fixture
def make_transaction(user, account, debit_rule):
    """Monta uma transação válida em débito, sem salvar, trocando o que o teste pedir."""
    def build(**fields):
        defaults = {
            'user': user,
            'account': account,
            'type': Type.OUT,
            'method': Method.DEBIT,
            'value': Decimal('10.00'),
            'occurred_at': date(2026, 9, 4),
        }
        return Transaction(**{**defaults, **fields})
    return build


@pytest.fixture
def make_installment(user, account, card):
    """Monta um parcelamento válido, sem salvar, trocando o que o teste pedir."""
    def build(**fields):
        defaults = {
            'user': user,
            'account': account,
            'card': card,
            'value': Decimal('1000.00'),
            'installments': 3,
            'occurred_at': date(2026, 9, 4),
        }
        return Installment(**{**defaults, **fields})
    return build


@pytest.fixture
def make_transfer(user, account, other_account, transfer_out_rule, transfer_in_rule):
    """Monta uma transferência válida entre as duas contas, sem salvar."""
    def build(**fields):
        defaults = {
            'user': user,
            'origin': account,
            'destination': other_account,
            'value': Decimal('250.00'),
            'occurred_at': date(2026, 9, 4),
        }
        return Transfer(**{**defaults, **fields})
    return build
