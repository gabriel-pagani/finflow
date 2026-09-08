"""O rótulo de categoria que a transação exibe, com e sem categoria."""


def test_com_categoria_mostra_a_descricao(make_transaction, category):
    assert make_transaction(category=category).category_display == 'Mercado'


def test_sem_categoria_mostra_o_rotulo_padrao(make_transaction):
    assert make_transaction().category_display == 'Categoria Não Identificada'


def test_str_da_transacao_usa_o_rotulo(make_transaction):
    assert str(make_transaction()) == 'Categoria Não Identificada (10.00)'
