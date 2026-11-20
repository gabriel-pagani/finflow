from datetime import date, datetime
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from app.models import BankAccount, BankConnection, BankTransaction, Installment, Method, Transaction, Type
from app.pluggy import PluggyError
from app.views import imports


Status = BankTransaction.Status


@pytest.fixture
def connection(user):
    # Recém-buscada: a tela não vai à Pluggy, a não ser que o teste peça.
    return BankConnection.objects.create(user=user, item_id='item-1', since=date(2026, 11, 1), synced_at=timezone.now())


@pytest.fixture
def bank_account(connection):
    return BankAccount.objects.create(connection=connection, external_id='acc-bank', kind=BankAccount.Kind.BANK, name='Conta Nubank')


@pytest.fixture
def card_account(connection):
    return BankAccount.objects.create(connection=connection, external_id='acc-card', kind=BankAccount.Kind.CREDIT, name='Cartão Nubank')


@pytest.fixture
def syncs(monkeypatch):
    calls = []
    monkeypatch.setattr(imports, 'sync', lambda connection: calls.append(connection) or 0)
    return calls


def bank_row(bank_account, external_id='t1', **fields):
    defaults = {
        'user': bank_account.connection.user, 'bank_account': bank_account, 'external_id': external_id,
        'type': Type.OUT, 'method': Method.DEBIT, 'value': Decimal('42.00'),
        'occurred_at': date(2026, 11, 10), 'description': 'Padaria',
    }
    return BankTransaction.objects.create(**{**defaults, **fields})


def parcel_row(card_account, number, value='100.01', **fields):
    return bank_row(
        card_account, f'p{number}', type=Type.OUT, method=Method.CREDIT, value=Decimal(value),
        description=f'Loja {number}/3', card_digits='1234', parcel=number, parcels=3,
        purchase_date=date(2026, 11, 5), purchase_key='2026-11-05|3|Loja', **fields,
    )


def approve(client, row, **data):
    return client.post(reverse('app:import_approve', args=[row.pk]), data)


def test_exige_login(client):
    response = client.get(reverse('app:imports'))
    assert response.status_code == 302 and 'login' in response['Location']


def test_menu_so_mostra_importar_com_conexao(logged, user, syncs):
    assert reverse('app:imports') not in logged.get(reverse('app:overview')).content.decode()
    BankConnection.objects.create(user=user, item_id='x', synced_at=timezone.now())
    assert reverse('app:imports') in logged.get(reverse('app:overview')).content.decode()


def test_busca_no_banco_so_quando_a_ultima_busca_envelheceu(logged, connection, syncs):
    logged.get(reverse('app:imports'))
    assert syncs == []

    BankConnection.objects.filter(pk=connection.pk).update(synced_at=None)
    logged.get(reverse('app:imports'))
    assert syncs == [connection]


def test_buscar_agora_busca_mesmo_recente(logged, connection, syncs):
    response = logged.post(reverse('app:imports'))
    assert response['Location'] == reverse('app:imports') and syncs == [connection]


@pytest.mark.parametrize('new, text', [(0, 'Nada novo no banco.'), (1, '1 transação nova do banco.'), (3, '3 transações novas do banco.')])
def test_buscar_agora_diz_o_que_chegou(logged, connection, monkeypatch, new, text):
    monkeypatch.setattr(imports, 'sync', lambda connection: new)

    response = logged.post(reverse('app:imports'))

    assert [str(m) for m in get_messages(response.wsgi_request)] == [text]


def test_falha_no_buscar_agora_so_mostra_o_erro(logged, connection, monkeypatch):
    def broken(connection):
        raise PluggyError('fora do ar')

    monkeypatch.setattr(imports, 'sync', broken)
    response = logged.post(reverse('app:imports'))

    assert [str(m) for m in get_messages(response.wsgi_request)] == [imports.SYNC_ERROR]


def test_falha_na_busca_avisa_e_mostra_o_que_ja_tem(logged, connection, bank_account, monkeypatch):
    def broken(connection):
        raise PluggyError('fora do ar')

    monkeypatch.setattr(imports, 'sync', broken)
    bank_row(bank_account)
    BankConnection.objects.filter(pk=connection.pk).update(synced_at=None)

    response = logged.get(reverse('app:imports'))

    assert response.status_code == 200
    assert [str(m) for m in get_messages(response.wsgi_request)] == [imports.SYNC_ERROR]
    assert 'Padaria' in response.content.decode()


def test_no_mesmo_dia_a_mais_recente_vem_primeiro(bank_account, syncs):
    # Comprou às 18:36 e às 19:05 (horário de Brasília); o id não diz a ordem.
    later = bank_row(bank_account, 't1', moment=datetime(2026, 11, 18, 22, 5, tzinfo=dt_timezone.utc))
    earlier = bank_row(bank_account, 't2', moment=datetime(2026, 11, 18, 21, 36, tzinfo=dt_timezone.utc))

    assert [item['row'] for item in imports.review_rows(bank_account.connection.user)] == [later, earlier]


def test_compra_parcelada_ordena_pelo_horario_da_compra(bank_account, card_account, syncs):
    purchase = parcel_row(card_account, 1, moment=datetime(2026, 11, 5, 15, 0, tzinfo=dt_timezone.utc))
    parcel_row(card_account, 2, moment=datetime(2026, 12, 5, 3, 0, tzinfo=dt_timezone.utc))
    same_day = bank_row(bank_account, 't1', occurred_at=date(2026, 11, 5), moment=datetime(2026, 11, 5, 18, 0, tzinfo=dt_timezone.utc))

    assert [item['row'] for item in imports.review_rows(bank_account.connection.user)] == [same_day, purchase]


def test_preenche_cartao_pelo_final_conta_do_cartao_e_categoria_do_historico(logged, card_account, card, category, make_transaction, syncs):
    make_transaction(description='padaria', category=category).save()
    row = bank_row(card_account, method=Method.CREDIT, card_digits='1234')

    item = imports.review_rows(row.user)[0]

    assert (item['card'], item['account'], item['category']) == (card.pk, card.account_id, category.pk)
    assert item['kind'] == 'transaction'


def test_compra_parcelada_aparece_uma_vez_com_o_total(logged, card_account, card, syncs):
    # A 1ª parcela leva a sobra dos centavos, como no parcelamento do FinFlow.
    parcel_row(card_account, 1, '100.02')
    parcel_row(card_account, 2)
    parcel_row(card_account, 3)

    items = imports.review_rows(card_account.connection.user)

    assert len(items) == 1
    item = items[0]
    assert (item['kind'], item['value'], item['installments'], item['occurred_at'], item['description']) == (
        'installment', Decimal('300.04'), 3, date(2026, 11, 5), 'Loja')


def test_avisa_de_transacao_parecida_ja_lancada(logged, bank_account, make_transaction, syncs):
    make_transaction(value=Decimal('42.00'), occurred_at=date(2026, 11, 12), description='Padaria do Zé').save()
    bank_row(bank_account)

    assert 'Parecida com 12/11/2026' in logged.get(reverse('app:imports')).content.decode()


def test_entrada_nao_e_parecida_com_saida_de_mesmo_valor(logged, bank_account, account, transfer_in_rule, make_transaction, syncs):
    # Aplicou 42 e recebeu 42 no mesmo dia: são movimentos opostos.
    make_transaction(type=Type.IN, method=Method.NOT_APPLICABLE, value=Decimal('42.00'), occurred_at=date(2026, 11, 10), account=account).save()
    bank_row(bank_account)

    assert imports.review_rows(bank_account.connection.user)[0]['similar'] is None


def test_lancar_cria_a_transacao_com_o_que_foi_ajustado(logged, bank_account, account, debit_rule, category, syncs):
    row = bank_row(bank_account)

    response = approve(logged, row, account=account.pk, type='OUT', method='DEBIT', nature='REGULAR',
                       category=category.pk, value='45.00', occurred_at='2026-11-10', description='Pão')

    assert response.status_code == 302
    transaction = Transaction.objects.get()
    assert (transaction.value, transaction.description, transaction.category) == (Decimal('45.00'), 'Pão', category)
    row.refresh_from_db()
    assert (row.status, row.transaction) == (Status.APPROVED, transaction)
    # A conta escolhida fica lembrada para as próximas desta conta do banco.
    bank_account.refresh_from_db()
    assert bank_account.account == account
    assert imports.review_rows(row.user) == []


def test_lancar_parcelamento_leva_todas_as_parcelas(logged, card_account, card, account, syncs):
    rows = [parcel_row(card_account, n) for n in (1, 2, 3)]

    approve(logged, rows[0], account=account.pk, card=card.pk, value='300.03', installments='3',
            occurred_at='2026-11-05', description='Loja')

    installment = Installment.objects.get()
    assert (installment.value, installment.installments, installment.transactions.count()) == (Decimal('300.03'), 3, 3)
    assert set(BankTransaction.objects.values_list('status', 'installment')) == {(Status.APPROVED, installment.pk)}


def test_formulario_invalido_nao_lanca_e_mantem_a_revisao(logged, bank_account, account, syncs):
    # Sem a regra de saída em débito, a conta recusa a combinação.
    row = bank_row(bank_account)

    approve(logged, row, account=account.pk, type='OUT', method='DEBIT', nature='REGULAR', value='42.00', occurred_at='2026-11-10')

    assert not Transaction.objects.exists()
    row.refresh_from_db()
    assert row.status == Status.PENDING


def test_descartar_tira_a_compra_inteira(logged, card_account, syncs):
    rows = [parcel_row(card_account, n) for n in (1, 2)]

    logged.post(reverse('app:import_discard', args=[rows[1].pk]))

    assert set(BankTransaction.objects.values_list('status', flat=True)) == {Status.DISCARDED}


def test_linha_de_outro_usuario_ou_ja_resolvida_da_404(logged, other_user, bank_account, syncs):
    other = BankConnection.objects.create(user=other_user, item_id='item-2')
    other_row = bank_row(BankAccount.objects.create(connection=other, external_id='acc-2', kind='BANK'), 'o1')
    done = bank_row(bank_account, 'd1', status=Status.APPROVED)

    for row in (other_row, done):
        assert logged.post(reverse('app:import_discard', args=[row.pk])).status_code == 404
        assert approve(logged, row).status_code == 404
