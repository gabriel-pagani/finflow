from . import proposals, queries
from .models import Kind


def ids(description):
    return {'type': 'array', 'items': {'type': 'integer'}, 'description': description}


def codes(values, description):
    return {'type': 'array', 'items': {'type': 'string', 'enum': list(values)}, 'description': description}


def nullable(schema):
    schema = {**schema, 'type': [schema['type'], 'null']}
    if 'enum' in schema:
        schema['enum'] = [*schema['enum'], None]
    return schema


FILTERS = {
    'date_field': {'type': 'string', 'enum': list(queries.DATE_FIELDS), 'description': 'Qual data recorta o período. effective_at (padrão) é a das telas: no crédito, o vencimento da fatura. occurred_at é o dia da compra.'},
    'start': {'type': 'string', 'description': 'Início do período, inclusivo, AAAA-MM-DD. null, sem limite.'},
    'end': {'type': 'string', 'description': 'Fim do período, inclusivo, AAAA-MM-DD. null, sem limite.'},
    'account': ids('Ids de conta.'),
    'category': ids('Ids de categoria.'),
    'uncategorized': {'type': 'boolean', 'description': 'Inclui as transações sem categoria. Combinado com category, soma as duas coisas.'},
    'card': ids('Ids de cartão.'),
    'type': codes(queries.Type.values, 'IN (entrada) e/ou OUT (saída). null, os dois.'),
    'method': codes(queries.Method.values, 'null, todos os métodos, inclusive o crédito.'),
    'nature': codes(queries.Nature.values, 'null, todas as naturezas. Receita e despesa no sentido das telas são REGULAR.'),
    'origin': codes(queries.ORIGINS, 'standalone (avulsa), installment (parcela) e/ou transfer (perna de transferência).'),
    'min_value': {'type': 'string', 'description': 'Valor mínimo de cada transação, com ponto: "100.00".'},
    'max_value': {'type': 'string', 'description': 'Valor máximo de cada transação, com ponto.'},
    'search': {'type': 'string', 'description': 'Trecho da descrição, sem diferenciar maiúsculas nem acentos.'},
}


# No modo estrito da API todo campo é obrigatório, e o modelo preenche o que não
# se aplica. Declarar tudo como anulável faz o enchimento vir como null, que as
# ferramentas leem como "não informado", em vez de um 0 ou "" que viraria dado.
def function(name, description, properties=None):
    properties = {key: nullable(schema) for key, schema in (properties or {}).items()}
    return {
        'type': 'function',
        'name': name,
        'description': description,
        'strict': True,
        'parameters': {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False},
    }


def proposal(name, kind, description, fields, clearable=()):
    properties = {
        'action': {'type': 'string', 'enum': [action.value for action in proposals.SPECS[kind].actions]},
        'id': {'type': 'integer', 'description': 'Id do registro, obrigatório para editar e apagar. null ao criar.'},
        **fields,
    }
    if clearable:
        properties['clear'] = codes(clearable, 'Só ao editar: campos a esvaziar. null limpa nada.')

    return function(
        name,
        f'{description} Campos que não se aplicam vão null. NÃO grava: valida com as mesmas regras da tela e mostra '
        f'ao usuário um card de confirmação com o que será feito. Só o clique dele em Confirmar grava.',
        properties,
    )


OCCURRED_AT = {'type': 'string', 'description': 'AAAA-MM-DD. No crédito é o dia da COMPRA; a data efetiva é calculada pelo ciclo do cartão. null ao criar, vale hoje.'}
VALUE = {'type': 'string', 'description': 'Sempre positivo, com ponto decimal: "25.90".'}
DESCRIPTION = {'type': 'string', 'description': 'Descrição livre.'}
CATEGORY = {'type': 'integer', 'description': 'Id da categoria.'}

EDIT_RULE = 'Ao editar, preencha só os campos que mudam; os demais vão null e continuam como estão. Ao apagar, só o id importa.'


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
        'quebrados por até dois eixos. Use para toda soma, comparação, média ou ranking. Filtro null não filtra.',
        {
            **FILTERS,
            'group_by': codes(queries.AXES, f'Até {queries.MAX_AXES} eixos. Ex.: ["month", "category"].'),
        },
    ),
    function(
        'listar_transacoes',
        'Transações uma a uma, com ids, datas, rótulos e a origem (installment_id do parcelamento ou '
        'transfer_id da transferência). Traz a contagem do recorte inteiro. Não some a lista: para totais use '
        'analisar_transacoes. Filtro null não filtra.',
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
            'account': ids('Ids de conta. null, todas.'),
            'until': {'type': 'string', 'description': 'Saldo até esta data efetiva, AAAA-MM-DD. null, sem limite.'},
        },
    ),
    proposal('propor_cartao', Kind.CARD, f'Cria, edita ou apaga um cartão do usuário. {EDIT_RULE}', {
        'account': {'type': 'integer', 'description': 'Id da conta do cartão; ela precisa aceitar saída em Crédito.'},
        'last_digits': {'type': 'string', 'description': 'Os quatro últimos dígitos.'},
        'closing_day': {'type': 'integer', 'description': 'Dia do fechamento da fatura, de 1 a 31.'},
        'due_day': {'type': 'integer', 'description': 'Dia do vencimento da fatura, de 1 a 31.'},
    }),
    proposal('propor_transacao', Kind.TRANSACTION, f'Cria, edita ou apaga uma transação avulsa pelo id dela. Parcela e perna de transferência não passam por aqui. {EDIT_RULE}', {
        'occurred_at': OCCURRED_AT,
        'account': {'type': 'integer', 'description': 'Id da conta.'},
        'type': {'type': 'string', 'enum': queries.Type.values, 'description': 'IN entrada, OUT saída.'},
        'method': {'type': 'string', 'enum': queries.Method.values, 'description': 'Precisa ser uma combinação aceita pela conta. CREDIT exige card.'},
        'card': {'type': 'integer', 'description': 'Id do cartão, só no crédito, da mesma conta.'},
        'nature': {'type': 'string', 'enum': queries.Nature.values, 'description': 'REGULAR (padrão) ou INTERNAL, que só mexe no saldo, como o ajuste para bater com o extrato; INTERNAL nunca é CREDIT e vai sem categoria. Entre duas contas, use propor_transferencia.'},
        'category': CATEGORY,
        'description': DESCRIPTION,
        'value': VALUE,
    }, clearable=('category', 'description')),
    proposal('propor_parcelamento', Kind.INSTALLMENT, 'Cria ou apaga uma compra parcelada no crédito. Para apagar, o id é o installment_id da listagem, não o id de uma parcela. Apagar leva junto todas as parcelas.', {
        'occurred_at': OCCURRED_AT,
        'account': {'type': 'integer', 'description': 'Id da conta.'},
        'card': {'type': 'integer', 'description': 'Id do cartão, da mesma conta.'},
        'category': CATEGORY,
        'description': DESCRIPTION,
        'value': {'type': 'string', 'description': 'Valor TOTAL da compra, não o da parcela, com ponto decimal.'},
        'installments': {'type': 'integer', 'description': 'Número de parcelas, de 2 a 360.'},
    }),
    proposal('propor_transferencia', Kind.TRANSFER, 'Cria ou apaga uma transferência entre duas contas do usuário. Para apagar, o id é o transfer_id da listagem, não o id de uma das pernas. Apagar leva junto as duas transações.', {
        'occurred_at': OCCURRED_AT,
        'origin': {'type': 'integer', 'description': 'Id da conta de origem.'},
        'destination': {'type': 'integer', 'description': 'Id da conta de destino, diferente da origem.'},
        'description': DESCRIPTION,
        'value': VALUE,
    }),
]

PROPOSERS = {
    'propor_cartao': Kind.CARD,
    'propor_transacao': Kind.TRANSACTION,
    'propor_parcelamento': Kind.INSTALLMENT,
    'propor_transferencia': Kind.TRANSFER,
}


def run(name, arguments, *, user, today, conversation):
    if name in PROPOSERS:
        return proposals.propose(PROPOSERS[name], user, conversation, arguments)

    readers = {
        'consultar_cadastros': lambda: queries.registry(user, today),
        'analisar_transacoes': lambda: queries.analyze_transactions(user, arguments),
        'listar_transacoes': lambda: queries.list_transactions(user, arguments),
        'consultar_saldo': lambda: queries.balance(user, arguments),
    }

    reader = readers.get(name)
    if reader is None:
        return {'ok': False, 'error': f'Não existe ferramenta {name!r}.'}, None

    try:
        return reader(), None
    except queries.QueryError as error:
        return {'ok': False, 'error': str(error)}, None
