from . import queries


def ids(description):
    return {'type': 'array', 'items': {'type': 'integer'}, 'description': description}


def codes(values, description):
    return {'type': 'array', 'items': {'type': 'string', 'enum': list(values)}, 'description': description}


FILTERS = {
    'date_field': {'type': 'string', 'enum': list(queries.DATE_FIELDS), 'description': 'Qual data recorta o período. effective_at (padrão) é a das telas: no crédito, o vencimento da fatura. occurred_at é o dia da compra.'},
    'start': {'type': 'string', 'description': 'Início do período, inclusivo, AAAA-MM-DD. Ausente, sem limite.'},
    'end': {'type': 'string', 'description': 'Fim do período, inclusivo, AAAA-MM-DD. Ausente, sem limite.'},
    'account': ids('Ids de conta.'),
    'category': ids('Ids de categoria.'),
    'uncategorized': {'type': 'boolean', 'description': 'Inclui as transações sem categoria. Combinado com category, soma as duas coisas.'},
    'card': ids('Ids de cartão.'),
    'type': codes(queries.Type.values, 'IN (entrada) e/ou OUT (saída). Ausente, os dois.'),
    'method': codes(queries.Method.values, 'Ausente, todos os métodos, inclusive o crédito.'),
    'nature': codes(queries.Nature.values, 'Ausente, todas as naturezas. Receita e despesa no sentido das telas são REGULAR.'),
    'origin': codes(queries.ORIGINS, 'standalone (avulsa), installment (parcela) e/ou transfer (perna de transferência).'),
    'min_value': {'type': 'string', 'description': 'Valor mínimo de cada transação, com ponto: "100.00".'},
    'max_value': {'type': 'string', 'description': 'Valor máximo de cada transação, com ponto.'},
    'search': {'type': 'string', 'description': 'Trecho da descrição, sem diferenciar maiúsculas nem acentos.'},
}


def function(name, description, properties=None):
    return {
        'type': 'function',
        'name': name,
        'description': description,
        'parameters': {'type': 'object', 'properties': properties or {}, 'additionalProperties': False},
    }


TOOLS = [
    function(
        'consultar_cadastros',
        'Contas (com as combinações de tipo e método que cada uma aceita), categorias, cartões do usuário '
        '(com fechamento, vencimento e a data em que uma compra de hoje seria cobrada) e os códigos de tipo, '
        'método e natureza. Chame antes de usar qualquer id.',
    ),
    function(
        'analisar_transacoes',
        'Totais de entrada, saída, saldo do recorte (net) e contagem, calculados no banco, opcionalmente '
        'quebrados por até dois eixos. Use para toda soma, comparação, média ou ranking.',
        {
            **FILTERS,
            'group_by': codes(queries.AXES, f'Até {queries.MAX_AXES} eixos. Ex.: ["month", "category"].'),
        },
    ),
    function(
        'listar_transacoes',
        'Transações uma a uma, com ids, datas, rótulos e a origem (parcelamento ou transferência). Traz a '
        'contagem do recorte inteiro. Não some a lista: para totais use analisar_transacoes.',
        {
            **FILTERS,
            'order': {'type': 'string', 'enum': list(queries.ORDERS), 'description': 'recent (padrão), oldest, largest ou smallest.'},
            'limit': {'type': 'integer', 'description': f'De 1 a {queries.MAX_LIMIT}. Padrão {queries.DEFAULT_LIMIT}.'},
            'offset': {'type': 'integer', 'description': 'Deslocamento para paginar.'},
        },
    ),
    function(
        'consultar_saldo',
        'Saldo acumulado por conta e total, igual ao card de Saldo da Visão Geral: todas as naturezas, só '
        'Débito e Não Se Aplica.',
        {
            'account': ids('Ids de conta. Ausente, todas.'),
            'until': {'type': 'string', 'description': 'Saldo até esta data efetiva, AAAA-MM-DD. Ausente, sem limite.'},
        },
    ),
]


def run(name, arguments, *, user, today):
    readers = {
        'consultar_cadastros': lambda: queries.registry(user, today),
        'analisar_transacoes': lambda: queries.analyze_transactions(user, arguments),
        'listar_transacoes': lambda: queries.list_transactions(user, arguments),
        'consultar_saldo': lambda: queries.balance(user, arguments),
    }

    reader = readers.get(name)
    if reader is None:
        return {'ok': False, 'error': f'Não existe ferramenta {name!r}.'}

    try:
        return reader()
    except queries.QueryError as error:
        return {'ok': False, 'error': str(error)}
