"""API para agentes externos: autenticação, consulta e escrita.

O que estes casos protegem é o contrato com o n8n. A escrita reusa os
formulários da interface, então a regra em si já é coberta pelos testes dela; o
que se verifica aqui é que a API realmente passa por eles — que ela não aceita o
que a tela recusa, e que o cálculo de fatura acontece também por esta porta.
"""

import json
from datetime import date
from decimal import Decimal

import pytest

from app.models import ApiToken, Method, Nature, Transaction, Type


CONTEXT_URL = '/api/context/'
CREATE_URL = '/api/transactions/'


@pytest.fixture
def alice_token(alice):
    token, raw = ApiToken.issue(alice, 'Agente n8n')
    return token, raw


@pytest.fixture
def auth(alice_token):
    _, raw = alice_token
    return {'authorization': f'Bearer {raw}'}


def post(client, payload, headers):
    return client.post(CREATE_URL, data=json.dumps(payload), content_type='application/json', headers=headers)


# --------------------------------------------------------------------------
# Autenticação
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_sem_cabecalho_recusa(client):
    response = client.get(CONTEXT_URL)
    assert response.status_code == 401
    assert response.json()['error'] == 'unauthorized'
    # Sem o WWW-Authenticate o 401 não diz ao cliente como se autenticar.
    assert response['WWW-Authenticate'].startswith('Bearer')


@pytest.mark.django_db
@pytest.mark.parametrize('header', ['Token abc', 'Bearer', 'Bearer    ', 'abc'])
def test_cabecalho_malformado_recusa(client, header):
    assert client.get(CONTEXT_URL, headers={'authorization': header}).status_code == 401


@pytest.mark.django_db
def test_token_invalido_recusa(client):
    assert client.get(CONTEXT_URL, headers={'authorization': 'Bearer finflow_nao-existe'}).status_code == 401


@pytest.mark.django_db
def test_token_revogado_recusa(client, alice_token, auth):
    token, _ = alice_token
    token.is_active = False
    token.save()
    assert client.get(CONTEXT_URL, headers=auth).status_code == 401


@pytest.mark.django_db
def test_usuario_inativo_recusa(client, alice, auth):
    alice.is_active = False
    alice.save()
    response = client.get(CONTEXT_URL, headers=auth)
    assert response.status_code == 403
    assert response.json()['error'] == 'forbidden'


@pytest.mark.django_db
def test_token_nao_e_gravado_em_claro(alice_token):
    token, raw = alice_token
    assert raw.startswith(ApiToken.PREFIX)
    assert token.digest != raw
    assert raw not in token.digest
    assert ApiToken.resolve(raw) == token


@pytest.mark.django_db
def test_uso_marca_o_token(client, alice_token, auth):
    token, _ = alice_token
    assert token.last_used is None
    client.get(CONTEXT_URL, headers=auth)
    token.refresh_from_db()
    assert token.last_used is not None


@pytest.mark.django_db
def test_metodo_errado_responde_json(client, auth):
    response = client.get(CREATE_URL, headers=auth)
    assert response.status_code == 405
    assert response.json()['error'] == 'method_not_allowed'


# --------------------------------------------------------------------------
# Consulta
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_consulta_traz_opcoes_e_documentacao(client, auth, account, category, business_rules):
    body = client.get(CONTEXT_URL, headers=auth).json()

    assert body['ok'] is True
    assert [a['description'] for a in body['options']['accounts']] == ['Conta Corrente', 'Poupanca']
    assert [c['description'] for c in body['options']['categories']] == ['Alimentacao']
    assert {t['value'] for t in body['options']['types']} == {'IN', 'OUT'}
    assert {m['value'] for m in body['options']['methods']} == {'CREDIT', 'DEBIT', 'NOT_APPLICABLE'}

    # A documentação é o que ensina o agente a montar o lançamento; sem ela a
    # consulta seria só uma lista de ids sem regra.
    assert 'ciclo_do_cartao' in body['documentation']
    assert 'parcelamento' in body['documentation']


@pytest.mark.django_db
def test_consulta_lista_combinacoes_permitidas(client, auth, account):
    from app.models import BusinessRule
    BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.DEBIT)

    body = client.get(CONTEXT_URL, headers=auth).json()
    conta = next(a for a in body['options']['accounts'] if a['id'] == account.id)

    assert conta['allowed_combinations'] == [{'type': 'OUT', 'method': 'DEBIT', 'label': 'Saída em Débito'}]


@pytest.mark.django_db
def test_consulta_so_enxerga_dados_do_dono(client, auth, alice, bob, make_transaction, make_card):
    make_transaction(alice, description='Da Alice')
    make_transaction(bob, description='Do Bob')
    make_card(bob, last_digits='9999')

    body = client.get(CONTEXT_URL, headers=auth).json()

    descricoes = {t['description'] for t in body['recent_transactions']}
    assert descricoes == {'Da Alice'}
    assert body['options']['cards'] == []


@pytest.mark.django_db
def test_consulta_explica_o_ciclo_do_cartao(client, auth, alice, make_card):
    make_card(alice)
    body = client.get(CONTEXT_URL, headers=auth).json()

    cartao = body['options']['cards'][0]
    assert cartao['closing_day'] == 20
    assert cartao['due_day'] == 27
    # As datas já calculadas poupam o agente de refazer a aritmética do ciclo.
    assert cartao['purchase_today']['due_date']
    assert 'fatura' in cartao['purchase_today']['explanation']


@pytest.mark.django_db
def test_consulta_soma_saldo_e_investimento(client, auth, alice, make_transaction, make_investment):
    make_transaction(alice, type=Type.IN, method=Method.DEBIT, value=Decimal('500.00'))
    make_transaction(alice, type=Type.OUT, method=Method.DEBIT, value=Decimal('200.00'))
    make_investment(alice, value=Decimal('1000.00'))

    body = client.get(CONTEXT_URL, headers=auth).json()

    # A aplicação gera uma saída de 1000, então o saldo em conta é 500-200-1000.
    assert Decimal(body['position']['balance']) == Decimal('-700.00')
    assert Decimal(body['position']['invested']) == Decimal('1000.00')


@pytest.mark.django_db
def test_analise_ignora_transferencia(client, auth, alice, make_transaction, make_transfer):
    make_transaction(alice, type=Type.OUT, method=Method.DEBIT, value=Decimal('100.00'))
    make_transfer(alice, value=Decimal('300.00'))

    body = client.get(CONTEXT_URL, headers=auth).json()

    # As duas pernas nascem INTERNAL: entram no saldo, mas não na análise, senão
    # o mesmo dinheiro apareceria como despesa e como receita.
    assert Decimal(body['analysis']['outcome']) == Decimal('100.00')
    assert Decimal(body['analysis']['income']) == Decimal('0.00')


# --------------------------------------------------------------------------
# Escrita — transação avulsa
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_cria_transacao_avulsa(client, auth, alice, account, category, business_rules):
    response = post(client, {
        'kind': 'transaction',
        'datetime': '2026-03-19T14:30',
        'account': account.id,
        'category': category.id,
        'type': 'OUT',
        'method': 'DEBIT',
        'description': 'Almoço',
        'value': '25.50',
    }, auth)

    assert response.status_code == 201, response.content
    body = response.json()
    assert body['ok'] is True

    transaction = Transaction.objects.get()
    assert transaction.user == alice
    assert transaction.value == Decimal('25.50')
    assert transaction.nature == Nature.REGULAR
    assert body['transactions'][0]['value'] == '25.50'


@pytest.mark.django_db
def test_valor_com_ponto_nao_vira_milhar(client, auth, account, business_rules):
    """"1234.56" tem de virar mil e duzentos, não cento e vinte e três mil.

    O locale é pt-br, onde o ponto é separador de milhar. Os campos do projeto
    não são localizados, então o ponto continua sendo decimal — este caso trava
    a regressão, que gravaria valor cem vezes maior sem nenhum erro.
    """
    response = post(client, {
        'account': account.id, 'type': 'OUT', 'method': 'DEBIT', 'value': '1234.56',
    }, auth)

    assert response.status_code == 201, response.content
    assert Transaction.objects.get().value == Decimal('1234.56')


@pytest.mark.django_db
def test_data_omitida_usa_o_agora(client, auth, account, business_rules):
    response = post(client, {'account': account.id, 'type': 'OUT', 'method': 'DEBIT', 'value': '10.00'}, auth)

    assert response.status_code == 201, response.content
    assert Transaction.objects.get().datetime is not None


@pytest.mark.django_db
def test_combinacao_sem_regra_de_negocio_recusa(client, auth, account):
    """Conta sem BusinessRule não aceita lançamento nenhum."""
    response = post(client, {
        'account': account.id, 'type': 'OUT', 'method': 'DEBIT', 'value': '10.00',
    }, auth)

    assert response.status_code == 422
    assert response.json()['error'] == 'validation_failed'
    assert not Transaction.objects.exists()


@pytest.mark.django_db
def test_credito_exige_cartao(client, auth, account, business_rules):
    response = post(client, {
        'account': account.id, 'type': 'OUT', 'method': 'CREDIT', 'value': '10.00',
    }, auth)

    assert response.status_code == 422
    assert 'card' in response.json()['errors']
    assert not Transaction.objects.exists()


@pytest.mark.django_db
def test_credito_grava_no_vencimento_da_fatura(client, auth, alice, account, business_rules, make_card):
    """A data enviada é a da compra; a gravada é a do vencimento da fatura."""
    card = make_card(alice)  # fecha dia 20, vence dia 27

    response = post(client, {
        'datetime': '2026-03-19T10:00',  # antes do fechamento: fatura de março
        'account': account.id, 'card': card.id,
        'type': 'OUT', 'method': 'CREDIT', 'value': '80.00',
    }, auth)

    assert response.status_code == 201, response.content
    assert Transaction.objects.get().datetime.date() == date(2026, 3, 27)


@pytest.mark.django_db
def test_compra_no_dia_do_fechamento_cai_na_fatura_seguinte(client, auth, alice, account, business_rules, make_card):
    card = make_card(alice)

    response = post(client, {
        'datetime': '2026-03-20T10:00',  # no fechamento: já é a fatura de abril
        'account': account.id, 'card': card.id,
        'type': 'OUT', 'method': 'CREDIT', 'value': '80.00',
    }, auth)

    assert response.status_code == 201, response.content
    assert Transaction.objects.get().datetime.date() == date(2026, 4, 27)


@pytest.mark.django_db
def test_cartao_de_outro_usuario_recusa(client, auth, bob, account, business_rules, make_card):
    alheio = make_card(bob, last_digits='9999')

    response = post(client, {
        'account': account.id, 'card': alheio.id,
        'type': 'OUT', 'method': 'CREDIT', 'value': '10.00',
    }, auth)

    assert response.status_code == 422
    assert 'card' in response.json()['errors']
    assert not Transaction.objects.exists()


@pytest.mark.django_db
def test_valor_negativo_recusa(client, auth, account, business_rules):
    response = post(client, {
        'account': account.id, 'type': 'OUT', 'method': 'DEBIT', 'value': '-5.00',
    }, auth)

    assert response.status_code == 422
    assert 'value' in response.json()['errors']


@pytest.mark.django_db
def test_campo_desconhecido_vira_aviso(client, auth, account, business_rules):
    response = post(client, {
        'account': account.id, 'type': 'OUT', 'method': 'DEBIT', 'value': '10.00',
        'valor': '999.00',  # nome errado: o agente precisa saber que foi ignorado
    }, auth)

    assert response.status_code == 201, response.content
    assert any('valor' in aviso for aviso in response.json()['warnings'])
    assert Transaction.objects.get().value == Decimal('10.00')


# --------------------------------------------------------------------------
# Escrita — parcelamento
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_cria_parcelamento_dividindo_o_total(client, auth, alice, account, business_rules, make_card):
    card = make_card(alice)

    response = post(client, {
        'kind': 'installment',
        'datetime': '2026-03-20T10:00',
        'account': account.id, 'card': card.id,
        'description': 'Notebook', 'value': '100.00', 'installments': 3,
    }, auth)

    assert response.status_code == 201, response.content
    parcelas = response.json()['transactions']

    # O total é dividido para baixo e a última parcela absorve a sobra.
    assert [p['value'] for p in parcelas] == ['33.33', '33.33', '33.34']
    assert sum(Decimal(p['value']) for p in parcelas) == Decimal('100.00')

    # Cada parcela sai do próprio ciclo: 27/06 é sábado e anda para segunda,
    # sem contaminar as outras.
    assert [p['datetime'][:10] for p in parcelas] == ['2026-04-27', '2026-05-27', '2026-06-29']


@pytest.mark.django_db
def test_parcelamento_exige_duas_parcelas(client, auth, alice, account, business_rules, make_card):
    card = make_card(alice)

    response = post(client, {
        'kind': 'installment', 'account': account.id, 'card': card.id,
        'value': '100.00', 'installments': 1,
    }, auth)

    assert response.status_code == 422
    assert not Transaction.objects.exists()


@pytest.mark.django_db
def test_parcelamento_exige_cartao(client, auth, account, business_rules):
    response = post(client, {
        'kind': 'installment', 'account': account.id, 'value': '100.00', 'installments': 3,
    }, auth)

    assert response.status_code == 422
    assert 'card' in response.json()['errors']


# --------------------------------------------------------------------------
# Escrita — transferência
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_cria_transferencia_com_duas_pernas(client, auth, account, destination_account, business_rules):
    response = post(client, {
        'kind': 'transfer',
        'origin': account.id, 'destination': destination_account.id,
        'description': 'Reserva', 'value': '300.00',
    }, auth)

    assert response.status_code == 201, response.content
    pernas = response.json()['transactions']

    assert len(pernas) == 2
    assert {p['nature']['value'] for p in pernas} == {'INTERNAL'}

    saida = next(p for p in pernas if p['type']['value'] == 'OUT')
    entrada = next(p for p in pernas if p['type']['value'] == 'IN')
    assert saida['method']['value'] == 'DEBIT'
    # O destino é "não se aplica": débito o jogaria no balde de receita real.
    assert entrada['method']['value'] == 'NOT_APPLICABLE'


@pytest.mark.django_db
def test_transferencia_para_a_mesma_conta_recusa(client, auth, account, business_rules):
    response = post(client, {
        'kind': 'transfer', 'origin': account.id, 'destination': account.id, 'value': '300.00',
    }, auth)

    assert response.status_code == 422
    assert not Transaction.objects.exists()


# --------------------------------------------------------------------------
# Corpo malformado
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_json_invalido_recusa(client, auth):
    response = client.post(CREATE_URL, data='{nao é json', content_type='application/json', headers=auth)
    assert response.status_code == 400
    assert response.json()['error'] == 'bad_request'


@pytest.mark.django_db
def test_kind_desconhecido_recusa(client, auth):
    response = post(client, {'kind': 'emprestimo'}, auth)
    assert response.status_code == 400
    assert 'emprestimo' in response.json()['message']


@pytest.mark.django_db
def test_corpo_que_nao_e_objeto_recusa(client, auth):
    response = client.post(CREATE_URL, data='[1, 2]', content_type='application/json', headers=auth)
    assert response.status_code == 400
