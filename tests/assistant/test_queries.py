from datetime import date
from decimal import Decimal

import pytest

from app.models import Category, Method, Nature, Transfer, Type
from assistant.queries import QueryError, analyze_transactions, balance, list_transactions, registry


def gravar(make_transaction, **fields):
    transaction = make_transaction(**fields)
    transaction.save()
    return transaction


def transferir(user, origin, destination, value):
    Transfer(user=user, origin=origin, destination=destination, value=Decimal(value), occurred_at=date(2026, 9, 4)).save()


def test_total_separa_entrada_de_saida(user, make_transaction):
    gravar(make_transaction, value=Decimal('30.10'))
    gravar(make_transaction, value=Decimal('19.95'))
    gravar(make_transaction, type=Type.IN, method=Method.NOT_APPLICABLE, value=Decimal('100.00'))

    total = analyze_transactions(user, {})['total']

    assert total == {'income': '100.00', 'outcome': '50.05', 'net': '49.95', 'count': 3}


def test_nao_soma_dinheiro_de_outro_usuario(user, other_user, make_transaction):
    gravar(make_transaction, value=Decimal('10.00'))
    gravar(make_transaction, user=other_user, value=Decimal('9999.00'))

    assert analyze_transactions(user, {})['total']['outcome'] == '10.00'
    assert list_transactions(user, {})['count'] == 1
    assert balance(user, {})['total']['outcome'] == '10.00'


def test_sem_filtro_de_natureza_entram_todas(user, account, other_account, make_transaction):
    gravar(make_transaction, value=Decimal('10.00'))
    transferir(user, account, other_account, '500.00')

    assert analyze_transactions(user, {})['total']['outcome'] == '510.00'
    assert analyze_transactions(user, {'nature': [Nature.REGULAR]})['total']['outcome'] == '10.00'


def test_quebra_soma_o_mesmo_total(user, account, make_transaction):
    mercado = Category.objects.create(description='Mercado')
    lazer = Category.objects.create(description='Lazer')
    gravar(make_transaction, category=mercado, value=Decimal('10.00'), occurred_at=date(2026, 8, 1))
    gravar(make_transaction, category=lazer, value=Decimal('20.00'), occurred_at=date(2026, 8, 15))
    gravar(make_transaction, category=mercado, value=Decimal('40.00'), occurred_at=date(2026, 9, 1))
    gravar(make_transaction, value=Decimal('5.00'), occurred_at=date(2026, 9, 2))

    payload = analyze_transactions(user, {'group_by': ['month', 'category']})

    grupos = {(row['keys']['month']['code'], row['keys']['category']['label']): row['outcome'] for row in payload['groups']}
    assert grupos == {
        ('2026-08', 'Lazer'): '20.00',
        ('2026-08', 'Mercado'): '10.00',
        ('2026-09', 'Mercado'): '40.00',
        ('2026-09', 'Categoria Não Identificada'): '5.00',
    }
    assert sum(Decimal(row['outcome']) for row in payload['groups']) == Decimal(payload['total']['outcome'])
    assert [row['keys']['month']['code'] for row in payload['groups']] == ['2026-08', '2026-08', '2026-09', '2026-09']


def test_data_efetiva_e_data_da_compra_recortam_diferente(user, card, make_transaction):
    compra = gravar(make_transaction, method=Method.CREDIT, card=card, occurred_at=date(2026, 8, 20))
    assert compra.effective_at.month == 9

    agosto = {'start': '2026-08-01', 'end': '2026-08-31'}
    assert analyze_transactions(user, agosto)['total']['count'] == 0
    assert analyze_transactions(user, {**agosto, 'date_field': 'occurred_at'})['total']['count'] == 1


def test_sem_categoria_soma_com_as_categorias_pedidas(user, category, make_transaction):
    outra = Category.objects.create(description='Lazer')
    gravar(make_transaction, category=category, value=Decimal('1.00'))
    gravar(make_transaction, category=outra, value=Decimal('2.00'))
    gravar(make_transaction, value=Decimal('4.00'))

    assert analyze_transactions(user, {'uncategorized': True})['total']['outcome'] == '4.00'
    assert analyze_transactions(user, {'category': [category.pk], 'uncategorized': True})['total']['outcome'] == '5.00'


def test_origem_separa_parcela_de_avulsa(user, make_transaction, make_installment):
    gravar(make_transaction, value=Decimal('10.00'))
    make_installment(value=Decimal('90.00'), installments=3).save()

    payload = analyze_transactions(user, {'group_by': ['origin']})

    assert {row['keys']['origin']['code']: row['outcome'] for row in payload['groups']} == {'installment': '90.00', 'standalone': '10.00'}


def test_busca_ignora_acento_e_caixa(user, make_transaction):
    gravar(make_transaction, description='Almoço no Centro')
    gravar(make_transaction, description='Mercado')

    assert analyze_transactions(user, {'search': 'ALMOCO'})['total']['count'] == 1


def test_eco_do_recorte_traz_os_rotulos(user, account, make_transaction):
    payload = analyze_transactions(user, {'account': [account.pk], 'nature': ['REGULAR']})

    assert payload['filters']['account'] == [{'id': account.pk, 'label': 'Nubank'}]
    assert payload['filters']['nature'] == [{'code': 'REGULAR', 'label': 'Normal'}]
    assert 'method' not in payload['filters']


@pytest.mark.parametrize('arguments', [
    {'start': '01/08/2026'},
    {'start': '2026-09-01', 'end': '2026-08-01'},
    {'account': [999999]},
    {'account': 'Nubank'},
    {'type': ['SAIDA']},
    {'min_value': '10,00'},
    {'min_value': '-1'},
    {'group_by': ['month', 'category', 'account']},
    {'group_by': ['semestre']},
    {'uncategorized': 'sim'},
])
def test_parametro_invalido_vira_erro_em_vez_de_ser_ignorado(user, arguments):
    with pytest.raises(QueryError):
        analyze_transactions(user, arguments)


def test_enchimento_do_modo_estrito_nao_filtra(user, make_transaction):
    make_transaction(value=Decimal('7.00')).save()
    arguments = {name: None for name in ('date_field', 'start', 'end', 'account', 'category', 'uncategorized', 'card', 'type',
                                         'method', 'nature', 'origin', 'min_value', 'max_value', 'search', 'group_by')}

    payload = analyze_transactions(user, arguments)

    assert payload['total']['outcome'] == '7.00'
    assert list_transactions(user, {**arguments, 'order': None, 'limit': None, 'offset': None})['count'] == 1


def test_cartao_de_outro_usuario_e_id_desconhecido(user, other_user_card):
    with pytest.raises(QueryError):
        analyze_transactions(user, {'card': [other_user_card.pk]})


def test_quebra_grande_e_cortada_sem_mexer_no_total(user, make_transaction, monkeypatch):
    monkeypatch.setattr('assistant.queries.MAX_GROUPS', 2)
    for day in (1, 2, 3):
        gravar(make_transaction, value=Decimal('1.00'), occurred_at=date(2026, 9, day))

    payload = analyze_transactions(user, {'group_by': ['day']})

    assert len(payload['groups']) == 2
    assert 'truncated' in payload
    assert payload['total']['outcome'] == '3.00'


def test_lista_avisa_quando_nao_veio_tudo(user, make_transaction):
    for value in ('1.00', '5.00', '3.00'):
        gravar(make_transaction, value=Decimal(value))

    payload = list_transactions(user, {'order': 'largest', 'limit': 2})

    assert payload['count'] == 3
    assert [row['value'] for row in payload['transactions']] == ['5.00', '3.00']
    assert 'has_more' in payload


def test_lista_identifica_a_origem_da_parcela(user, make_installment):
    parcelamento = make_installment(installments=3)
    parcelamento.save()

    rows = list_transactions(user, {'order': 'oldest'})['transactions']

    assert rows[0]['origin'] == {'kind': 'installment', 'id': parcelamento.pk, 'parcel': 1, 'parcels': 3}


def test_saldo_segue_a_visao_geral(user, account, other_account, card, make_transaction):
    gravar(make_transaction, value=Decimal('10.00'))
    gravar(make_transaction, method=Method.CREDIT, card=card, value=Decimal('700.00'))
    transferir(user, account, other_account, '100.00')

    payload = balance(user, {})

    por_conta = {row['label']: row['net'] for row in payload['accounts']}
    assert por_conta == {'Nubank': '-110.00', 'Itaú': '100.00'}
    assert payload['total']['net'] == '-10.00'


def test_cadastro_traz_combinacoes_e_so_os_proprios_cartoes(user, card, other_user_card):
    payload = registry(user, date(2026, 9, 4))

    nubank = next(account for account in payload['accounts'] if account['id'] == card.account_id)
    assert {'type': {'code': 'OUT', 'label': 'Saída'}, 'method': {'code': 'CREDIT', 'label': 'Crédito'}} in nubank['allowed_combinations']
    assert [item['id'] for item in payload['cards']] == [card.pk]
    assert payload['cards'][0]['purchase_today_charged_at'] == card.charge_date(date(2026, 9, 4)).isoformat()
