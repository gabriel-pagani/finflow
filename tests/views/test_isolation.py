"""Nenhuma tela alcança dado de outro usuário — leitura, escrita ou agregação.

Cada teste cria um registro do `other_user` e confere, logado como `user`, que
ele não aparece. Os painéis entram aqui junto das listagens porque neles o
vazamento é silencioso: um 404 na edição salta aos olhos, mas um total que soma
o dinheiro de outra pessoa desenha um gráfico plausível e não acusa nada.

O recorte de período vai explícito em todo GET. Sem isso os testes passariam a
depender do ano em que a suíte roda, já que o padrão das telas é o ano corrente.

Compartilhamento de dados, se um dia existir, é uma decisão consciente do dono e
tem que quebrar estes testes — eles são o contrato de hoje, não um acidente.
"""
from decimal import Decimal

from django.db.models import Sum
from django.urls import reverse

from app.models import Card, Installment, Transaction

ANO = {'start': '2026-01-01', 'end': '2026-12-31'}


def alheia(make_transaction, other_user, **fields):
    transaction = make_transaction(user=other_user, **fields)
    transaction.save()
    return transaction


def total_gravado():
    """Soma de todas as transações do banco, de todos os donos.

    Serve de contraprova: sem ela, um teste passaria igual se o registro alheio
    nunca tivesse sido criado, e não estaria provando isolamento nenhum.
    """
    return Transaction.objects.aggregate(total=Sum('value'))['total']


# Listagens ------------------------------------------------------------------

def test_listagem_de_transacoes_so_mostra_as_minhas(logged, make_transaction, other_user, debit_rule):
    minha = make_transaction()
    minha.save()
    alheia(make_transaction, other_user, value=Decimal('5000.00'))

    response = logged.get(reverse('app:transactions_list'), ANO)

    assert Transaction.objects.count() == 2
    assert list(response.context['object_list']) == [minha]


def test_busca_por_descricao_nao_alcanca_outro_usuario(logged, make_transaction, other_user, debit_rule):
    alheia(make_transaction, other_user, description='Segredo')

    response = logged.get(reverse('app:transactions_list'), {**ANO, 'search': 'segredo'})

    # A busca casa com ela no banco; o que a esconde da tela é só o dono.
    assert Transaction.objects.filter(description__icontains='segredo').exists()
    assert list(response.context['object_list']) == []


def test_listagem_de_cartoes_so_mostra_os_meus(logged, card, other_user_card):
    response = logged.get(reverse('app:cards_list'))

    assert Card.objects.count() == 2
    assert list(response.context['object_list']) == [card]


# Painéis --------------------------------------------------------------------

def test_visao_geral_nao_soma_valor_de_outro_usuario(logged, make_transaction, other_user, debit_rule):
    minha = make_transaction(value=Decimal('10.00'))
    minha.save()
    alheia(make_transaction, other_user, value=Decimal('5000.00'))

    cards = logged.get(reverse('app:overview'), ANO).context['cards']

    # As duas são idênticas fora o dono e o valor: se a minha entra no recorte,
    # a dela também entraria, e o que a barra é o filtro de usuário.
    assert total_gravado() == Decimal('5010.00')
    assert cards['outcome'] == 10.0
    assert cards['balance'] == -10.0


def test_grafico_de_categorias_nao_junta_o_gasto_de_outro_usuario(logged, make_transaction, other_user, category, debit_rule):
    minha = make_transaction(value=Decimal('10.00'), category=category)
    minha.save()
    # Mesma categoria de propósito: se o isolamento falhar, os dois viram uma
    # fatia só de 5.010,00 em vez de duas fatias distintas.
    alheia(make_transaction, other_user, value=Decimal('5000.00'), category=category)

    response = logged.get(reverse('app:overview'), ANO)

    assert total_gravado() == Decimal('5010.00')
    assert response.context['chart_categories'] == [{'name': str(category), 'value': 10.0}]


def test_grafico_mensal_nao_soma_valor_de_outro_usuario(logged, make_transaction, other_user, debit_rule):
    minha = make_transaction(value=Decimal('10.00'))
    minha.save()
    alheia(make_transaction, other_user, value=Decimal('5000.00'))

    series = logged.get(reverse('app:overview'), ANO).context['chart_months']['series']
    saida = next(item for item in series if item['name'] == 'Saída')

    assert total_gravado() == Decimal('5010.00')
    assert sum(saida['data']) == 10.0


def test_previsao_nao_soma_valor_de_outro_usuario(logged, make_transaction, other_user, card, other_user_card, credit_rule):
    minha = make_transaction(value=Decimal('10.00'), method='CREDIT', card=card)
    minha.save()
    alheia(make_transaction, other_user, value=Decimal('5000.00'), method='CREDIT', card=other_user_card)

    response = logged.get(reverse('app:forecast'), ANO)

    assert total_gravado() == Decimal('5010.00')
    assert response.context['total'] == 10.0


# Escrita por pk -------------------------------------------------------------

def test_nao_edita_transacao_de_outro_usuario(logged, make_transaction, other_user, account, debit_rule):
    transaction = alheia(make_transaction, other_user)

    response = logged.post(reverse('app:transaction_update', args=[transaction.pk]), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'type': 'OUT',
        'method': 'DEBIT', 'nature': 'REGULAR', 'value': '99.00',
    })

    assert response.status_code == 404
    transaction.refresh_from_db()
    assert transaction.value == Decimal('10.00')


def test_nao_apaga_transacao_de_outro_usuario(logged, make_transaction, other_user, debit_rule):
    transaction = alheia(make_transaction, other_user)

    assert logged.post(reverse('app:transaction_delete', args=[transaction.pk])).status_code == 404
    assert Transaction.objects.filter(pk=transaction.pk).exists()


def test_nao_edita_cartao_de_outro_usuario(logged, other_user_card, account, credit_rule):
    response = logged.post(reverse('app:card_update', args=[other_user_card.pk]), {
        'account': account.pk, 'last_digits': '9999', 'closing_day': '9', 'due_day': '19',
    })

    assert response.status_code == 404
    other_user_card.refresh_from_db()
    assert other_user_card.last_digits == '5678'


def test_nao_apaga_cartao_de_outro_usuario(logged, other_user_card):
    assert logged.post(reverse('app:card_delete', args=[other_user_card.pk])).status_code == 404
    assert Card.objects.filter(pk=other_user_card.pk).exists()


# Escrita usando recurso alheio ----------------------------------------------

def test_nao_lanca_transacao_no_cartao_de_outro_usuario(logged, account, other_user_card, credit_rule):
    response = logged.post(reverse('app:transaction_create'), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'type': 'OUT',
        'method': 'CREDIT', 'card': other_user_card.pk, 'nature': 'REGULAR', 'value': '10.00',
    }, follow=True)

    assert not Transaction.objects.exists()
    assert any('escolha válida' in str(message) for message in response.context['messages'])


def test_nao_parcela_no_cartao_de_outro_usuario(logged, account, other_user_card, credit_rule):
    response = logged.post(reverse('app:installment_create'), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'card': other_user_card.pk,
        'value': '300.00', 'installments': '3',
    }, follow=True)

    assert not Installment.objects.exists()
    assert not Transaction.objects.exists()
    # A mensagem é a do campo, e não a do Installment.clean(): quem barrou foi o
    # queryset do formulário. O model é a segunda barreira, coberta em
    # tests/models/test_installment_clean.py.
    assert any('escolha válida' in str(message) for message in response.context['messages'])


def test_registro_criado_pertence_a_quem_esta_logado(logged, user, other_user, account, debit_rule):
    # O formulário não expõe o dono; mandá-lo no POST não muda nada.
    logged.post(reverse('app:transaction_create'), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'type': 'OUT',
        'method': 'DEBIT', 'nature': 'REGULAR', 'value': '10.00', 'user': other_user.pk,
    })

    assert Transaction.objects.get().user == user
