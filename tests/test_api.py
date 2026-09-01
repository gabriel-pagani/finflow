"""API para agentes externos: autenticação, consulta e escrita.

O que estes casos protegem é o contrato com o n8n. A escrita reusa os
formulários da interface, então a regra em si já é coberta pelos testes dela; o
que se verifica aqui é que a API realmente passa por eles — que ela não aceita o
que a tela recusa, e que o cálculo de fatura acontece também por esta porta.

Na consulta, o que se protege é outra coisa: que cada rota carregue só o seu
assunto (é disso que sai a economia de tokens do agente) e que um filtro pedido
seja de fato o filtro aplicado. Um recorte que o agente pede e não recebe é pior
do que um erro: ele apresenta ao usuário um número de outro conjunto.
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from app.models import ApiToken, Method, Nature, Transaction, Type


INDEX_URL = '/api/'
DOCUMENTATION_URL = '/api/documentation/'
OPTIONS_URL = '/api/options/'
ANALYTICS_URL = '/api/analytics/'
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
# Consulta — rotas separadas
#
# A separação existe para o agente não pagar a documentação inteira a cada
# pergunta. Um caso que só olhasse o conteúdo deixaria a regressão passar: o que
# se verifica aqui é também o que cada rota NÃO carrega.
# --------------------------------------------------------------------------

def get(client, url, headers, **params):
    return client.get(url, params, headers=headers).json()


@pytest.mark.django_db
def test_indice_lista_as_rotas(client, auth):
    body = get(client, INDEX_URL, auth)

    assert body['ok'] is True
    assert 'GET /api/analytics/' in body['endpoints']


@pytest.mark.django_db
def test_documentacao_traz_as_regras(client, auth):
    body = get(client, DOCUMENTATION_URL, auth)

    # A documentação é o que ensina o agente a montar o lançamento; sem ela a
    # consulta seria só uma lista de ids sem regra.
    assert 'ciclo_do_cartao' in body['documentation']
    assert 'parcelamento' in body['documentation']
    assert 'analise' in body['documentation']


@pytest.mark.django_db
def test_documentacao_devolve_so_as_secoes_pedidas(client, auth):
    body = get(client, DOCUMENTATION_URL, auth, sections='ciclo_do_cartao,erros')

    assert set(body['documentation']) == {'ciclo_do_cartao', 'erros'}
    # O índice das seções vai junto: sem ele o agente não sabe o que mais existe
    # para pedir, e acaba baixando tudo por precaução.
    assert 'parcelamento' in body['sections']


@pytest.mark.django_db
def test_documentacao_recusa_secao_inexistente(client, auth):
    response = client.get(DOCUMENTATION_URL, {'sections': 'inventada'}, headers=auth)

    assert response.status_code == 400
    assert 'inventada' in response.json()['message']


@pytest.mark.django_db
def test_opcoes_nao_carregam_a_documentacao(client, auth, account, category, business_rules):
    body = get(client, OPTIONS_URL, auth)

    assert [a['description'] for a in body['options']['accounts']] == ['Conta Corrente', 'Poupanca']
    assert [c['description'] for c in body['options']['categories']] == ['Alimentacao']
    assert {t['value'] for t in body['options']['types']} == {'IN', 'OUT'}
    assert {m['value'] for m in body['options']['methods']} == {'CREDIT', 'DEBIT', 'NOT_APPLICABLE'}
    # É esta ausência que separa esta rota da antiga /api/context/.
    assert 'documentation' not in body


@pytest.mark.django_db
def test_opcoes_listam_combinacoes_permitidas(client, auth, account):
    from app.models import BusinessRule
    BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.DEBIT)

    body = get(client, OPTIONS_URL, auth)
    conta = next(a for a in body['options']['accounts'] if a['id'] == account.id)

    assert conta['allowed_combinations'] == [{'type': 'OUT', 'method': 'DEBIT', 'label': 'Saída em Débito'}]


@pytest.mark.django_db
def test_opcoes_explicam_o_ciclo_do_cartao(client, auth, alice, make_card):
    make_card(alice)
    cartao = get(client, OPTIONS_URL, auth)['options']['cards'][0]

    assert cartao['closing_day'] == 20
    assert cartao['due_day'] == 27
    # As datas já calculadas poupam o agente de refazer a aritmética do ciclo.
    assert cartao['purchase_today']['due_date']
    assert 'fatura' in cartao['purchase_today']['explanation']


@pytest.mark.django_db
def test_analise_nao_carrega_documentacao_nem_opcoes(client, auth, alice, make_transaction):
    make_transaction(alice)
    body = get(client, ANALYTICS_URL, auth)

    assert set(body) >= {'summary', 'breakdowns', 'position', 'filters'}
    assert 'documentation' not in body
    assert 'options' not in body
    # Transação é a seção mais cara e por isso não vem sem ser pedida.
    assert 'transactions' not in body


@pytest.mark.django_db
def test_analise_so_enxerga_dados_do_dono(client, auth, alice, bob, make_transaction, make_card):
    make_transaction(alice, description='Da Alice')
    make_transaction(bob, description='Do Bob')
    make_card(bob, last_digits='9999')

    body = get(client, ANALYTICS_URL, auth, include='transactions')

    assert {t['description'] for t in body['transactions']} == {'Da Alice'}
    assert get(client, OPTIONS_URL, auth)['options']['cards'] == []


@pytest.mark.django_db
def test_analise_soma_saldo_e_investimento(client, auth, alice, make_transaction, make_investment):
    make_transaction(alice, type=Type.IN, method=Method.DEBIT, value=Decimal('500.00'))
    make_transaction(alice, type=Type.OUT, method=Method.DEBIT, value=Decimal('200.00'))
    make_investment(alice, value=Decimal('1000.00'))

    body = get(client, ANALYTICS_URL, auth)

    # A aplicação gera uma saída de 1000, então o saldo em conta é 500-200-1000.
    assert Decimal(body['position']['balance']) == Decimal('-700.00')
    assert Decimal(body['position']['invested']) == Decimal('1000.00')


@pytest.mark.django_db
def test_analise_ignora_transferencia(client, auth, alice, make_transaction, make_transfer):
    make_transaction(alice, type=Type.OUT, method=Method.DEBIT, value=Decimal('100.00'))
    make_transfer(alice, value=Decimal('300.00'))

    body = get(client, ANALYTICS_URL, auth)

    # As duas pernas nascem INTERNAL: entram no saldo, mas não na análise, senão
    # o mesmo dinheiro apareceria como despesa e como receita.
    assert Decimal(body['summary']['outcome']) == Decimal('100.00')
    assert Decimal(body['summary']['income']) == Decimal('0.00')


# --------------------------------------------------------------------------
# Consulta — filtros da análise
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_filtros_padrao_sao_declarados_na_resposta(client, auth, alice, make_transaction):
    """Os padrões desta API não são óbvios, e por isso viajam na resposta.

    Sem esse eco o agente narra como "todos os gastos" um total que exclui o
    cartão e as movimentações internas — e ninguém tem como perceber.
    """
    make_transaction(alice)
    filtros = get(client, ANALYTICS_URL, auth)['filters']

    assert filtros['method'] == ['DEBIT', 'NOT_APPLICABLE']
    assert filtros['nature'] == ['REGULAR']
    assert {'method', 'nature'} <= set(filtros['defaulted'])


@pytest.mark.django_db
def test_filtro_de_categoria(client, auth, alice, category, make_transaction):
    from app.models import Category
    outra = Category.objects.create(description='Transporte')

    make_transaction(alice, category=category, value=Decimal('30.00'))
    make_transaction(alice, category=outra, value=Decimal('70.00'))

    body = get(client, ANALYTICS_URL, auth, category=str(outra.id))

    assert Decimal(body['summary']['outcome']) == Decimal('70.00')
    assert body['filters']['category'] == [outra.id]


@pytest.mark.django_db
def test_filtro_de_categoria_alcanca_o_sem_categoria(client, auth, alice, make_transaction):
    make_transaction(alice, category=None, value=Decimal('40.00'))
    make_transaction(alice, value=Decimal('60.00'))

    body = get(client, ANALYTICS_URL, auth, category='null')

    assert Decimal(body['summary']['outcome']) == Decimal('40.00')
    linha = body['breakdowns']['category'][0]
    assert linha['label'] == 'Categoria Não Identificada'


@pytest.mark.django_db
def test_filtro_de_conta(client, auth, alice, account, destination_account, make_transaction):
    make_transaction(alice, account=account, value=Decimal('10.00'))
    make_transaction(alice, account=destination_account, value=Decimal('90.00'))

    body = get(client, ANALYTICS_URL, auth, account=str(destination_account.id))

    assert Decimal(body['summary']['outcome']) == Decimal('90.00')


@pytest.mark.django_db
def test_filtro_de_metodo_alcanca_o_credito(client, auth, alice, make_transaction):
    """O crédito fica fora do padrão, mas tem de estar ao alcance de um pedido.

    Sem isto não haveria como analisar a fatura: a previsão só olha para a
    frente, e o gasto de crédito já vencido ficaria invisível.
    """
    make_transaction(alice, method=Method.DEBIT, value=Decimal('20.00'))
    make_transaction(alice, method=Method.CREDIT, value=Decimal('80.00'))

    padrao = get(client, ANALYTICS_URL, auth)
    credito = get(client, ANALYTICS_URL, auth, method='CREDIT')
    tudo = get(client, ANALYTICS_URL, auth, method='all')

    assert Decimal(padrao['summary']['outcome']) == Decimal('20.00')
    assert Decimal(credito['summary']['outcome']) == Decimal('80.00')
    assert Decimal(tudo['summary']['outcome']) == Decimal('100.00')
    assert tudo['filters']['method'] == 'todos'


@pytest.mark.django_db
def test_filtro_de_natureza_alcanca_a_transferencia(client, auth, alice, make_transfer):
    make_transfer(alice, value=Decimal('300.00'))

    body = get(client, ANALYTICS_URL, auth, nature='INTERNAL')

    # As duas pernas: a saída na origem e a entrada no destino.
    assert Decimal(body['summary']['outcome']) == Decimal('300.00')
    assert Decimal(body['summary']['income']) == Decimal('300.00')


@pytest.mark.django_db
def test_filtro_de_origem(client, auth, alice, make_transaction, make_transfer):
    make_transaction(alice, value=Decimal('25.00'))
    make_transfer(alice, value=Decimal('300.00'))

    avulsas = get(client, ANALYTICS_URL, auth, nature='all', origin='standalone')
    pernas = get(client, ANALYTICS_URL, auth, nature='all', origin='transfer')

    assert Decimal(avulsas['summary']['outcome']) == Decimal('25.00')
    assert Decimal(pernas['summary']['outcome']) == Decimal('300.00')


@pytest.mark.django_db
def test_filtro_de_faixa_de_valor(client, auth, alice, make_transaction):
    make_transaction(alice, value=Decimal('10.00'))
    make_transaction(alice, value=Decimal('100.00'))
    make_transaction(alice, value=Decimal('1000.00'))

    body = get(client, ANALYTICS_URL, auth, min_value='50', max_value='500')

    assert Decimal(body['summary']['outcome']) == Decimal('100.00')
    assert body['summary']['transactions'] == 1


@pytest.mark.django_db
def test_filtro_de_descricao(client, auth, alice, make_transaction):
    make_transaction(alice, description='Padaria da esquina', value=Decimal('12.00'))
    make_transaction(alice, description='Posto', value=Decimal('200.00'))

    body = get(client, ANALYTICS_URL, auth, search='padaria')

    assert Decimal(body['summary']['outcome']) == Decimal('12.00')


@pytest.mark.django_db
def test_periodo_explicito_alcanca_o_historico_antigo(client, auth, alice, make_transaction):
    """Um recorte de data chega onde a janela de meses não chega."""
    antiga = timezone.make_aware(datetime(2020, 5, 15, 10, 0))
    make_transaction(alice, datetime=antiga, value=Decimal('55.00'))
    make_transaction(alice, value=Decimal('7.00'))

    body = get(client, ANALYTICS_URL, auth, start='2020-05', end='2020-05')

    assert Decimal(body['summary']['outcome']) == Decimal('55.00')
    # "2020-05" no fim do recorte estica para o último dia do mês; cortá-lo no
    # dia 1 deixaria maio inteiro de fora da própria consulta de maio.
    assert body['filters']['period'] == {'start': '2020-05-01', 'end': '2020-05-31'}


@pytest.mark.django_db
def test_janela_de_meses_abre_no_primeiro_dia_do_mes(client, auth, alice, make_transaction):
    make_transaction(alice)
    filtros = get(client, ANALYTICS_URL, auth, months='1')['filters']

    hoje = timezone.localdate()
    assert filtros['period']['start'] == hoje.replace(day=1).isoformat()
    assert filtros['period']['months'] == 1


@pytest.mark.django_db
def test_meses_recortam_o_que_ficou_para_tras(client, auth, alice, make_transaction):
    passado = timezone.now() - timedelta(days=120)
    make_transaction(alice, datetime=passado, value=Decimal('500.00'))
    make_transaction(alice, value=Decimal('25.00'))

    assert Decimal(get(client, ANALYTICS_URL, auth, months='1')['summary']['outcome']) == Decimal('25.00')
    assert Decimal(get(client, ANALYTICS_URL, auth, months='12')['summary']['outcome']) == Decimal('525.00')


@pytest.mark.django_db
def test_eixos_pedidos_quebram_o_mesmo_total(client, auth, alice, account, destination_account, make_transaction):
    make_transaction(alice, account=account, value=Decimal('30.00'))
    make_transaction(alice, account=destination_account, value=Decimal('70.00'))

    body = get(client, ANALYTICS_URL, auth, group_by='account,method,month')
    quebras = body['breakdowns']

    assert list(quebras) == ['account', 'method', 'month']
    # Eixos diferentes são recortes do mesmo conjunto: cada um soma o total.
    for eixo in quebras.values():
        assert sum(Decimal(linha['outcome']) for linha in eixo) == Decimal('100.00')


@pytest.mark.django_db
def test_top_recolhe_a_cauda_sem_perder_o_total(client, auth, alice, make_transaction):
    from app.models import Category
    for indice, valor in enumerate(['10.00', '20.00', '30.00'], start=1):
        categoria = Category.objects.create(description=f'Categoria {indice}')
        make_transaction(alice, category=categoria, value=Decimal(valor))

    linhas = get(client, ANALYTICS_URL, auth, group_by='category', top='1')['breakdowns']['category']

    assert [linha['key'] for linha in linhas][-1] == 'others'
    assert linhas[0]['outcome'] == '30.00'
    # A cauda vira uma linha, e não sai da resposta: um total que não fecha faz o
    # agente inventar a diferença.
    assert sum(Decimal(linha['outcome']) for linha in linhas) == Decimal('60.00')


@pytest.mark.django_db
def test_lista_ordenada_pelos_maiores(client, auth, alice, make_transaction):
    make_transaction(alice, value=Decimal('10.00'))
    make_transaction(alice, value=Decimal('900.00'))
    make_transaction(alice, value=Decimal('50.00'))

    body = get(client, ANALYTICS_URL, auth, include='summary,transactions', order='largest', limit='2')

    assert [t['value'] for t in body['transactions']] == ['900.00', '50.00']
    # O resumo continua contando o conjunto inteiro, não a página.
    assert body['summary']['transactions'] == 3


@pytest.mark.django_db
def test_limite_zero_conta_sem_listar(client, auth, alice, make_transaction):
    make_transaction(alice)
    body = get(client, ANALYTICS_URL, auth, include='summary,transactions', limit='0')

    assert body['transactions'] == []
    assert body['summary']['transactions'] == 1


@pytest.mark.django_db
def test_previsao_obedece_ao_filtro_de_conta(client, auth, alice, account, destination_account, make_transaction):
    futuro = timezone.now() + timedelta(days=30)
    make_transaction(alice, account=account, method=Method.CREDIT, datetime=futuro, value=Decimal('200.00'))
    make_transaction(alice, account=destination_account, method=Method.CREDIT, datetime=futuro, value=Decimal('800.00'))

    body = get(client, ANALYTICS_URL, auth, include='forecast', account=str(account.id))

    assert Decimal(body['forecast']['total']) == Decimal('200.00')
    # A previsão diz o próprio recorte: ela não obedece ao período nem ao método.
    assert 'crédito' in body['forecast']['scope']


@pytest.mark.django_db
@pytest.mark.parametrize('params, trecho', [
    ({'method': 'PIX'}, 'method'),
    ({'nature': 'QUALQUER'}, 'nature'),
    ({'group_by': 'trimestre'}, 'group_by'),
    ({'include': 'tudo_junto'}, 'include'),
    ({'origin': 'emprestimo'}, 'origin'),
    ({'start': '31-02-2026'}, 'start'),
    ({'account': 'conta'}, 'account'),
    ({'months': 'doze'}, 'months'),
    ({'min_value': 'muito'}, 'min_value'),
    ({'order': 'aleatoria'}, 'order'),
    ({'start': '2026-06', 'end': '2026-01'}, 'depois'),
])
def test_filtro_invalido_explica_o_que_aceita(client, auth, params, trecho):
    """Filtro errado vira erro, não silêncio.

    Ignorar o parâmetro devolveria 200 com o recorte errado, e o agente
    apresentaria ao usuário o total de um conjunto que ninguém pediu. Com a
    mensagem dizendo o que se aceita, ele refaz a chamada sozinho.
    """
    response = client.get(ANALYTICS_URL, params, headers=auth)

    assert response.status_code == 400
    assert response.json()['error'] == 'invalid_filter'
    assert trecho in response.json()['message']


# --------------------------------------------------------------------------
# Consulta — a rota antiga
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_contexto_continua_entregando_tudo(client, auth, alice, account, category, business_rules, make_transaction):
    """A composição das outras rotas, para o fluxo antigo não quebrar no deploy."""
    make_transaction(alice, description='Da Alice')

    body = get(client, CONTEXT_URL, auth)

    assert 'ciclo_do_cartao' in body['documentation']
    assert body['options']['accounts']
    assert set(body) >= {'summary', 'breakdowns', 'position', 'forecast', 'transactions'}
    assert [t['description'] for t in body['transactions']] == ['Da Alice']


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
