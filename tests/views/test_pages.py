from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.models import Category


PERIODO = {'start': '2026-01-01', 'end': '2026-12-31'}


def seen_by(route, card):
    """O que cada página enxerga.

    Os painéis seguem os métodos da página, então o lançamento que alimenta o
    filtro precisa ser do método certo: a previsão só olha crédito, e crédito
    pede cartão.
    """
    return {'method': 'CREDIT', 'card': card} if route == 'app:forecast' else {}


def panel(response, name):
    return next(item for item in response.context['panels'] if item['name'] == name)


def options(response, name):
    """Os rótulos que o painel oferece, na ordem em que aparecem na tela."""
    return [option['label'] for option in panel(response, name)['options']]


def available(response, name):
    """Só os rótulos que ainda têm transação sob os outros filtros."""
    return [option['label'] for option in panel(response, name)['options'] if option['available']]


@pytest.mark.parametrize('route', ['app:overview', 'app:forecast', 'app:transactions_list', 'app:cards_list'])
def test_paginas_renderizam(logged, route, card, category, transfer_in_rule):
    response = logged.get(reverse(route))
    assert response.status_code == 200, response.content[:500]


def test_login_renderiza(client):
    assert client.get(reverse('app:login')).status_code == 200


def test_filtros_e_busca(logged, account, category, debit_rule, make_transaction):
    make_transaction(category=category, description='Almoço').save()

    url = reverse('app:transactions_list')
    assert logged.get(url, {'search': 'almoco', 'account': account.pk, 'type': 'OUT', 'method': 'DEBIT', 'nature': 'REGULAR',
                            'start': '2026-01-01', 'end': '2026-12-31'}).status_code == 200


def test_filtro_com_valor_invalido_e_descartado(logged, debit_rule):
    assert logged.get(reverse('app:transactions_list'), {'account': 'lixo', 'category': 'x', 'type': 'ZZ', 'nature': 'ZZ'}).status_code == 200


def test_filtro_por_natureza(logged, account, debit_rule, make_transaction):
    normal = make_transaction()
    normal.save()
    interna = make_transaction(nature='INTERNAL')
    interna.save()

    response = logged.get(reverse('app:transactions_list'), {'nature': 'INTERNAL', 'start': '2026-01-01', 'end': '2026-12-31'})
    assert list(response.context['object_list']) == [interna]
    assert '<td>Interna</td>' in response.content.decode()


def test_interna_entra_no_saldo_e_fica_fora_dos_graficos_e_kpis(logged, account, category, debit_rule, make_transaction):
    make_transaction(category=category, value='10.00').save()
    make_transaction(nature='INTERNAL', value='500.00').save()

    response = logged.get(reverse('app:overview'), {'start': '2026-01-01', 'end': '2026-12-31'})
    saida = next(item for item in response.context['chart_months']['series'] if item['name'] == 'Saída')

    assert response.context['cards']['outcome'] == 10.0
    assert response.context['cards']['balance'] == -510.0
    assert sum(saida['data']) == 10.0
    assert response.context['chart_categories'] == [{'name': str(category), 'value': 10.0}]


@pytest.mark.parametrize('route', ['app:overview', 'app:forecast', 'app:transactions_list'])
def test_filtro_oferece_so_contas_e_categorias_em_uso(logged, route, account, other_account, category, card,
                                                     other_user, make_transaction):
    lazer = Category.objects.create(description='Lazer')
    make_transaction(category=category, **seen_by(route, card)).save()
    make_transaction(user=other_user, account=other_account, category=lazer).save()

    response = logged.get(reverse(route), PERIODO)

    assert options(response, 'account') == [str(account)]
    assert options(response, 'category') == [str(category)]


@pytest.mark.parametrize('route', ['app:overview', 'app:forecast', 'app:transactions_list'])
def test_filtro_so_oferece_nao_identificada_com_transacao_sem_categoria(logged, route, category, card,
                                                                       other_user, other_account, make_transaction):
    visivel = seen_by(route, card)
    make_transaction(category=category, **visivel).save()
    # A de outro usuário não conta: a opção segue o que é meu.
    make_transaction(user=other_user, account=other_account).save()
    assert 'Categoria Não Identificada' not in options(logged.get(reverse(route), PERIODO), 'category')

    make_transaction(**visivel).save()
    assert 'Categoria Não Identificada' in options(logged.get(reverse(route), PERIODO), 'category')


@pytest.mark.parametrize('route', ['app:overview', 'app:forecast'])
def test_filtro_do_dashboard_segue_os_metodos_da_pagina(logged, route, account, other_account, category,
                                                       other_account_card, make_transaction):
    lazer = Category.objects.create(description='Lazer')
    make_transaction(category=category).save()
    make_transaction(account=other_account, card=other_account_card, method='CREDIT', category=lazer).save()

    response = logged.get(reverse(route), PERIODO)

    # A visão geral só olha débito e não se aplica; a previsão, só crédito.
    conta, categoria = {'app:overview': (account, category), 'app:forecast': (other_account, lazer)}[route]
    assert options(response, 'account') == [str(conta)]
    assert options(response, 'category') == [str(categoria)]


def test_nao_identificada_do_dashboard_segue_os_metodos_da_pagina(logged, other_account, other_account_card,
                                                                 make_transaction):
    make_transaction(account=other_account, card=other_account_card, method='CREDIT').save()

    assert options(logged.get(reverse('app:overview'), PERIODO), 'category') == []
    assert options(logged.get(reverse('app:forecast'), PERIODO), 'category') == ['Categoria Não Identificada']


def test_marcar_conta_recorta_as_categorias_oferecidas(logged, account, other_account, category,
                                                      other_debit_rule, make_transaction):
    lazer = Category.objects.create(description='Lazer')
    make_transaction(category=category).save()
    make_transaction(account=other_account, category=lazer).save()

    url = reverse('app:transactions_list')

    # Sem conta escolhida as duas categorias estão em jogo.
    assert available(logged.get(url, PERIODO), 'category') == [str(lazer), str(category)]

    response = logged.get(url, {**PERIODO, 'account': account.pk})

    # Marcada a conta, sobra a categoria que existe nela — e as contas seguem
    # inteiras na lista, senão trocar a conta escolhida ficaria impossível.
    assert available(response, 'category') == [str(category)]
    assert available(response, 'account') == [str(other_account), str(account)]


def test_periodo_recorta_as_opcoes_oferecidas(logged, account, category, debit_rule, make_transaction):
    lazer = Category.objects.create(description='Lazer')
    make_transaction(category=category, occurred_at=date(2026, 3, 10)).save()
    make_transaction(category=lazer, occurred_at=date(2026, 9, 4)).save()

    response = logged.get(reverse('app:transactions_list'), {'start': '2026-01-01', 'end': '2026-06-30'})

    assert available(response, 'category') == [str(category)]


def test_previsao_so_oferece_o_que_ainda_esta_por_vir(logged, account, category, card, make_transaction):
    hoje = timezone.localdate()
    antiga = Category.objects.create(description='Antiga')
    make_transaction(category=antiga, method='CREDIT', card=card, occurred_at=hoje - timedelta(days=120)).save()
    make_transaction(category=category, method='CREDIT', card=card, occurred_at=hoje + timedelta(days=30)).save()

    # Sem período na URL a previsão olha de hoje em diante, e o filtro segue o
    # mesmo recorte: o que já foi pago não é previsão de gasto nenhum.
    assert options(logged.get(reverse('app:forecast')), 'category') == [str(category)]


def test_filtro_de_metodo_recorta_as_contas_oferecidas(logged, account, other_account, other_account_card,
                                                      make_transaction):
    make_transaction().save()
    make_transaction(account=other_account, card=other_account_card, method='CREDIT').save()

    url = reverse('app:transactions_list')

    assert available(logged.get(url, {**PERIODO, 'method': 'CREDIT'}), 'account') == [str(other_account)]
    assert available(logged.get(url, {**PERIODO, 'method': 'DEBIT'}), 'account') == [str(account)]


def test_escolha_sem_dado_continua_marcada_e_sinalizada(logged, account, other_account, category,
                                                       other_debit_rule, make_transaction):
    lazer = Category.objects.create(description='Lazer')
    make_transaction(category=category).save()
    make_transaction(account=other_account, category=lazer).save()

    response = logged.get(reverse('app:transactions_list'),
                          {**PERIODO, 'account': other_account.pk, 'category': category.pk})

    # A categoria não existe na conta marcada, mas some da lista seria pior: a
    # escolha continua valendo, e é ela que deixou o resultado vazio.
    mercado = next(option for option in panel(response, 'category')['options'] if option['label'] == str(category))
    assert mercado['selected'] is True
    assert mercado['available'] is False
    assert list(response.context['object_list']) == []


def test_filtro_junta_categoria_e_nao_identificada(logged, category, debit_rule, make_transaction):
    lazer = Category.objects.create(description='Lazer')
    make_transaction(category=category).save()
    no_lazer = make_transaction(category=lazer)
    no_lazer.save()
    sem_categoria = make_transaction()
    sem_categoria.save()

    url = reverse('app:transactions_list')
    periodo = {'start': '2026-01-01', 'end': '2026-12-31'}

    response = logged.get(url, {**periodo, 'category': [lazer.pk, 'none']})
    assert set(response.context['object_list']) == {no_lazer, sem_categoria}

    response = logged.get(url, {**periodo, 'category': 'none'})
    assert list(response.context['object_list']) == [sem_categoria]


def test_grafico_de_categorias_respeita_a_nao_identificada(logged, category, debit_rule, make_transaction):
    make_transaction(category=category).save()
    make_transaction(value='30.00').save()

    response = logged.get(reverse('app:overview'), {'start': '2026-01-01', 'end': '2026-12-31', 'category': 'none'})

    assert response.context['chart_categories'] == [{'name': 'Categoria Não Identificada', 'value': 30.0}]
