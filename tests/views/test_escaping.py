"""
O que o usuário digita volta para a tela como texto, e nunca como marcação.

O escape é do próprio Django e vale enquanto ninguém desligar o autoescape nem
marcar um valor como seguro; estes testes existem para que isso seja notado no
dia em que acontecer.
"""
from django.urls import reverse


SCRIPT = '<script>alert(1)</script>'

# Fecha o atributo e abre um manipulador de evento: é o que um data-attribute
# mal escapado entregaria, sem precisar de nenhum sinal de menor.
BREAKOUT = '" onmouseover="alert(1)'

PERIOD = {'start': '2026-01-01', 'end': '2026-12-31'}


def listing(client, **params):
    return client.get(reverse('app:transactions_list'), {**PERIOD, **params}).content.decode()


def test_descricao_com_html_sai_escapada_na_celula(logged, make_transaction):
    make_transaction(description=SCRIPT).save()

    page = listing(logged)

    assert SCRIPT not in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page


def test_descricao_nao_escapa_do_data_attribute(logged, make_transaction):
    make_transaction(description=BREAKOUT).save()

    page = listing(logged)

    # As aspas escapadas mantêm o valor inteiro dentro do atributo; o que não
    # pode aparecer é o manipulador com aspa de verdade abrindo depois.
    assert 'onmouseover="' not in page
    assert 'data-description="&quot; onmouseover=&quot;alert(1)"' in page


def test_termo_buscado_volta_escapado_no_campo(logged):
    page = listing(logged, search=SCRIPT)

    assert SCRIPT not in page
    assert 'value="&lt;script&gt;alert(1)&lt;/script&gt;"' in page


def test_erro_de_formulario_com_html_sai_escapado_no_aviso(logged, account, debit_rule):
    """O erro de escolha inválida repete o que foi enviado, e ele vira aviso na tela."""
    response = logged.post(reverse('app:transaction_create'), {
        'occurred_at': '2026-09-04',
        'account': account.pk,
        'type': SCRIPT,
        'method': 'DEBIT',
        'nature': 'REGULAR',
        'description': 'Mercado',
        'value': '25.50',
    }, follow=True)

    page = response.content.decode()

    assert SCRIPT not in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page
