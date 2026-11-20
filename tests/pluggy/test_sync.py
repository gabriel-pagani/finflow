import urllib.error
from datetime import date
from decimal import Decimal

import pytest

from app import pluggy
from app.models import BankAccount, BankConnection, BankTransaction, Method, Type


BANK = {'id': 'acc-bank', 'type': 'BANK', 'name': 'Conta Corrente'}
CARD = {'id': 'acc-card', 'type': 'CREDIT', 'name': 'Cartão', 'marketingName': 'Nubank Ultravioleta'}


def raw(id, amount, type, day='2026-11-10', description='Mercado', **meta):
    return {
        'id': id, 'amount': amount, 'type': type, 'date': f'{day}T12:00:00.000Z',
        'description': description, 'category': 'Groceries',
        'creditCardMetadata': meta or None,
    }


def parcel(id, number, total=3, amount=100.0, purchase='2026-11-05', day=None, description='Loja'):
    return raw(id, amount, 'DEBIT', day or purchase, f'{description} {number}/{total}',
               cardNumber='3553', installmentNumber=number, totalInstallments=total,
               purchaseDate=f'{purchase}T15:00:00.000Z')


class FakeApi:
    """Responde como a Pluggy: contas da conexão e transações por conta."""

    def __init__(self):
        self.accounts = [BANK, CARD]
        self.transactions = {'acc-bank': [], 'acc-card': []}
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, params))
        if path == '/accounts':
            return {'results': self.accounts}
        return {'results': self.transactions[params['accountId']], 'next': None}


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(pluggy, 'get', fake)
    return fake


@pytest.fixture
def connection(user):
    return BankConnection.objects.create(user=user, item_id='item-1', since=date(2026, 11, 1))


def rows():
    return {row.external_id: row for row in BankTransaction.objects.all()}


def test_traduz_direcao_metodo_e_valor_dos_dois_tipos_de_conta(api, connection):
    api.transactions['acc-bank'] = [raw('b-out', -50.5, 'DEBIT'), raw('b-in', 1000, 'CREDIT')]
    api.transactions['acc-card'] = [raw('c-out', 80, 'DEBIT', cardNumber='3553'), raw('c-in', -80, 'CREDIT')]

    assert pluggy.sync(connection) == 4
    assert pluggy.sync(connection) == 0

    got = rows()
    assert (got['b-out'].type, got['b-out'].method, got['b-out'].value) == (Type.OUT, Method.DEBIT, Decimal('50.50'))
    assert (got['b-in'].type, got['b-in'].method) == (Type.IN, Method.NOT_APPLICABLE)
    assert (got['c-out'].type, got['c-out'].method, got['c-out'].card_digits) == (Type.OUT, Method.CREDIT, '3553')
    assert (got['c-in'].type, got['c-in'].method, got['c-in'].value) == (Type.IN, Method.NOT_APPLICABLE, Decimal('80'))
    assert got['b-out'].bank_category == 'Groceries' and got['b-out'].user == connection.user
    assert BankAccount.objects.get(external_id='acc-card').name == 'Nubank Ultravioleta'
    assert connection.synced_at is not None


def test_data_em_utc_vira_o_dia_de_brasilia(api, connection):
    api.transactions['acc-bank'] = [{**raw('t', -1, 'DEBIT'), 'date': '2026-11-14T01:00:00.000Z'}]

    pluggy.sync(connection)

    row = rows()['t']
    assert row.occurred_at == date(2026, 11, 13)
    assert row.moment.isoformat() == '2026-11-14T01:00:00+00:00'


def test_ignora_o_que_e_anterior_a_conexao(api, connection):
    api.transactions['acc-bank'] = [raw('velha', -1, 'DEBIT', day='2026-10-31'), raw('nova', -1, 'DEBIT', day='2026-11-01')]
    # Compra parcelada antes da data de corte: as parcelas novas também ficam de
    # fora, porque a compra já devia estar lançada à mão.
    api.transactions['acc-card'] = [parcel('p2', 2, purchase='2026-10-20', day='2026-11-20')]

    pluggy.sync(connection)

    assert set(rows()) == {'nova'}


def test_segue_o_cursor_ate_a_ultima_pagina(monkeypatch, connection):
    pages = {
        None: {'results': [raw('a', -1, 'DEBIT')], 'next': '?accountId=acc-bank&after=CURSOR'},
        'CURSOR': {'results': [raw('b', -1, 'DEBIT')], 'next': None},
    }

    def fake(path, params):
        if path == '/accounts':
            return {'results': [BANK]}
        return pages[params.get('after')]

    monkeypatch.setattr(pluggy, 'get', fake)
    pluggy.sync(connection)

    assert set(rows()) == {'a', 'b'}


def test_parcelas_da_mesma_compra_dividem_a_chave_sem_o_sufixo(api, connection):
    api.transactions['acc-card'] = [parcel('p1', 1), parcel('p2', 2, day='2026-12-05')]

    pluggy.sync(connection)

    got = rows()
    assert got['p1'].purchase_key == got['p2'].purchase_key == '2026-11-05|3|Loja'
    assert (got['p2'].parcel, got['p2'].parcels, got['p2'].purchase_date) == (2, 3, date(2026, 11, 5))


def test_atualiza_so_o_que_ainda_esta_a_revisar(api, connection):
    api.transactions['acc-bank'] = [raw('pend', -10, 'DEBIT'), raw('lanc', -10, 'DEBIT')]
    pluggy.sync(connection)
    BankTransaction.objects.filter(external_id='lanc').update(status=BankTransaction.Status.APPROVED)

    api.transactions['acc-bank'] = [raw('pend', -12, 'DEBIT', description='Mercado X'), raw('lanc', -12, 'DEBIT')]
    pluggy.sync(connection)

    got = rows()
    assert (got['pend'].value, got['pend'].description) == (Decimal('12'), 'Mercado X')
    assert got['lanc'].value == Decimal('10')


def test_o_que_some_do_banco_sai_da_revisao_mas_o_lancado_fica(api, connection):
    api.transactions['acc-bank'] = [raw('pend', -10, 'DEBIT'), raw('lanc', -10, 'DEBIT')]
    pluggy.sync(connection)
    BankTransaction.objects.filter(external_id='lanc').update(status=BankTransaction.Status.APPROVED)

    api.transactions['acc-bank'] = []
    pluggy.sync(connection)

    assert set(rows()) == {'lanc'}


@pytest.mark.parametrize('status', [BankTransaction.Status.APPROVED, BankTransaction.Status.DISCARDED])
def test_parcela_nova_segue_o_destino_da_compra(api, connection, make_installment, status):
    installment = make_installment()
    installment.save()
    api.transactions['acc-card'] = [parcel('p1', 1)]
    pluggy.sync(connection)
    BankTransaction.objects.filter(external_id='p1').update(status=status, installment=installment if status == BankTransaction.Status.APPROVED else None)

    api.transactions['acc-card'] = [parcel('p1', 1), parcel('p2', 2, day='2026-12-05')]
    pluggy.sync(connection)

    new = rows()['p2']
    assert new.status == status
    assert new.installment == (installment if status == BankTransaction.Status.APPROVED else None)


def test_a_chave_da_api_e_reaproveitada(monkeypatch, connection, settings):
    settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    calls = []

    def fake_request(path, *, key=None, body=None):
        calls.append(path.split('?')[0])
        if path == '/auth':
            return {'apiKey': 'chave'}
        if path.startswith('/accounts'):
            return {'results': []}

    monkeypatch.setattr(pluggy, 'request', fake_request)
    pluggy.sync(connection)
    pluggy.sync(connection)

    assert calls.count('/auth') == 1 and calls.count('/accounts') == 2


def test_falha_de_rede_vira_pluggy_error(monkeypatch):
    def broken(*args, **kwargs):
        raise urllib.error.URLError('sem rede')

    monkeypatch.setattr(pluggy.urllib.request, 'urlopen', broken)
    with pytest.raises(pluggy.PluggyError):
        pluggy.request('/auth', body={})
