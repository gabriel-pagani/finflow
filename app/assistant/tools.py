"""As ferramentas do assistente: funções, não rotas.

O agente do n8n falava com este sistema por HTTP, e por isso precisava de token,
de uma API exposta e de uma camada que traduzisse erro de formulário em código de
status. Rodando dentro do processo, nada disso é necessário: a ferramenta é uma
chamada de função, e o usuário é um argumento.

Isso muda o isolamento de lugar. Antes, "o agente só vê os dados do dono do
token" era uma regra escrita numa view, que alguém poderia esquecer de repetir na
próxima rota. Agora é a assinatura: `run()` recebe `user` e o repassa a consultas
que nunca são montadas sem dono. Não há prompt que faça o modelo pedir o extrato
de outra pessoa, porque não existe parâmetro para isso.

A escrita não grava. `registrar_lancamento` valida no mesmo formulário da tela e
para num PendingWrite, que só vira dinheiro no banco quando o usuário clica.
"""

from django.utils import timezone
from django.utils.formats import date_format, number_format

from ..forms import InstallmentForm, TransactionForm, TransferForm
from ..models import Method, Nature, PendingWrite, Type
from .data import AnalyticsFilters, FilterError, analytics, options


# O que cada lançamento é, e com qual formulário se valida. São os mesmos três
# do botão "Nova Transação" da interface, com as mesmas regras: o que a tela
# recusa, o assistente recusa com a mesma frase.
KINDS = {
    'transaction': {'form': TransactionForm, 'label': 'Transação'},
    'installment': {'form': InstallmentForm, 'label': 'Parcelamento'},
    'transfer': {'form': TransferForm, 'label': 'Transferência'},
}


TOOLS = [
    {
        'type': 'function',
        'name': 'consultar_opcoes',
        'description': (
            'O que existe para escolher num lançamento: contas (com as combinações de tipo e '
            'método que cada uma aceita), categorias, cartões (com a fatura e o vencimento que '
            'uma compra feita hoje receberia) e os códigos válidos de tipo, método e natureza. '
            'Chame antes de montar qualquer lançamento e antes de responder sobre cartões.'
        ),
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
    {
        'type': 'function',
        'name': 'consultar_financas',
        'description': (
            'Toda pergunta sobre dinheiro: saldo, investimentos, entradas, saídas, gastos por '
            'categoria, conta, cartão ou método, previsão de crédito a vencer, e a lista de '
            'transações. Filtre pelo recorte exato da pergunta em vez de pedir tudo.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'start': {'type': 'string', 'description': 'Início do recorte, inclusivo. Aceita "2026-03-15", "2026-03" (mês inteiro), "2026" (ano inteiro) e "15/03/2026".'},
                'end': {'type': 'string', 'description': 'Fim do recorte, inclusivo. Mesmos formatos de start. Sem ele, vale hoje.'},
                'months': {'type': 'string', 'description': 'Alternativa a start: janela móvel de N meses cheios terminando no mês de end. "1" é o mês corrente. Ignorado se start vier.'},
                'account': {'type': 'string', 'description': 'Ids de conta, separados por vírgula.'},
                'category': {'type': 'string', 'description': 'Ids de categoria, separados por vírgula. Aceita "null" para transações sem categoria.'},
                'card': {'type': 'string', 'description': 'Ids de cartão, separados por vírgula. Aceita "null" para o que não passou em cartão.'},
                'type': {'type': 'string', 'description': 'IN, OUT, ou os dois separados por vírgula. Padrão: os dois.'},
                'method': {'type': 'string', 'description': 'CREDIT, DEBIT, NOT_APPLICABLE. PADRÃO: DEBIT,NOT_APPLICABLE (o dinheiro que já saiu). Para o cartão use CREDIT; para tudo, "all".'},
                'nature': {'type': 'string', 'description': 'REGULAR, INTERNAL, ADJUSTMENT. PADRÃO: REGULAR. Para incluir transferências, INTERNAL ou "all".'},
                'origin': {'type': 'string', 'description': 'standalone, installment, transfer ou investment. Responde "quanto do meu gasto é parcelado".'},
                'min_value': {'type': 'string', 'description': 'Valor mínimo de cada transação, não do total.'},
                'max_value': {'type': 'string', 'description': 'Valor máximo de cada transação, não do total.'},
                'search': {'type': 'string', 'description': 'Trecho contido na descrição.'},
                'group_by': {'type': 'string', 'description': 'Eixos das quebras, separados por vírgula: month, day, week, year, category, account, card, method, type, nature. Padrão: month,category.'},
                'include': {'type': 'string', 'description': 'Seções da resposta: summary, breakdowns, position, forecast, transactions. Padrão: summary,breakdowns,position. Peça só o que for usar — transactions é a mais cara.'},
                'top': {'type': 'string', 'description': 'Limita cada quebra não temporal às N maiores linhas, somando o resto em "outros".'},
                'limit': {'type': 'string', 'description': 'Tamanho da lista de transactions. "0" devolve só a contagem.'},
                'offset': {'type': 'string', 'description': 'Deslocamento da lista de transactions.'},
                'order': {'type': 'string', 'description': 'recent (padrão), oldest, largest ou smallest. Use largest para "meus maiores gastos".'},
                'forecast_months': {'type': 'string', 'description': 'Horizonte da previsão, em meses. Padrão 12.'},
            },
            'additionalProperties': False,
        },
    },
    {
        'type': 'function',
        'name': 'registrar_lancamento',
        'description': (
            'Monta um lançamento e o apresenta ao usuário para confirmação. NÃO grava nada: '
            'quem grava é o clique do usuário no cartão de confirmação. Depois de chamar, peça '
            'a confirmação — nunca diga que o lançamento foi registrado.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'kind': {'type': 'string', 'enum': ['transaction', 'installment', 'transfer'], 'description': 'transaction: lançamento único de entrada ou saída. installment: compra no crédito parcelada (mínimo 2 parcelas; o valor é o TOTAL da compra). transfer: movimentação entre duas contas do próprio usuário.'},
                'datetime': {'type': 'string', 'description': 'Data e hora. Aceita "2026-08-31T14:30" e "31/08/2026 14:30". Omitido, usa o agora. No crédito, esta é a data da COMPRA: o lançamento é gravado no vencimento da fatura.'},
                'account': {'type': 'integer', 'description': 'Id da conta. Obrigatório em transaction e installment.'},
                'type': {'type': 'string', 'enum': ['IN', 'OUT'], 'description': 'Só em transaction. O que distingue entrada de saída — o valor é sempre positivo.'},
                'method': {'type': 'string', 'enum': ['CREDIT', 'DEBIT', 'NOT_APPLICABLE'], 'description': 'Só em transaction. Em CREDIT o cartão é obrigatório.'},
                'card': {'type': 'integer', 'description': 'Id do cartão. Obrigatório no crédito e em installment. Precisa pertencer à mesma conta do lançamento.'},
                'nature': {'type': 'string', 'enum': ['REGULAR', 'INTERNAL', 'ADJUSTMENT'], 'description': 'Só em transaction. Padrão REGULAR. Use ADJUSTMENT apenas para corrigir divergência com o extrato real, nunca para mascarar despesa ou receita normal.'},
                'category': {'type': 'integer', 'description': 'Id da categoria. Opcional.'},
                'description': {'type': 'string', 'description': 'Descrição do lançamento.'},
                'value': {'type': 'string', 'description': 'Sempre positivo, com PONTO como separador decimal: "25.00". Em installment, é o valor TOTAL da compra, não o da parcela.'},
                'installments': {'type': 'integer', 'description': 'Só em installment. Número de parcelas, mínimo 2.'},
                'origin': {'type': 'integer', 'description': 'Só em transfer. Id da conta de origem.'},
                'destination': {'type': 'integer', 'description': 'Só em transfer. Id da conta de destino, diferente da origem.'},
            },
            'required': ['kind'],
            'additionalProperties': False,
        },
    },
]


# --------------------------------------------------------------------------
# Consulta
# --------------------------------------------------------------------------

def is_filler(value):
    """Valor que o modelo pôs só para não deixar o campo vazio.

    Um esquema que declara todos os campos leva o modelo a mandar todos, mesmo
    os que não valem para o que ele está montando — e o que não vale vem com um
    valor de enchimento: string vazia, ou zero.

    Zero precisa ser descartado junto com o vazio, e não repassado adiante: não
    é id de conta, de cartão nem de categoria, e não é número de parcelas. Se
    ele chegasse ao formulário viraria "escolha inválida", o lançamento correto
    seria recusado, e o modelo — sem entender o que errou — repetiria a mesma
    chamada até esgotar as rodadas. Foi exatamente o que aconteceu.
    """
    return value is None or value == '' or value == 0


def as_params(arguments):
    """Argumentos do modelo no formato que os filtros leem.

    Os filtros nasceram lendo querystring, onde tudo é texto. O modelo às vezes
    manda `months: 6` em vez de `"6"`, e um int não tem .split(): a coerção aqui
    evita que a consulta quebre por causa do tipo que o modelo escolheu, que não
    é o que ele deveria estar pensando.
    """
    return {key: str(value) for key, value in arguments.items() if not is_filler(value)}


def consultar_opcoes(user, today, arguments):
    return {'options': options(user, today)}


def consultar_financas(user, today, arguments):
    filters = AnalyticsFilters(as_params(arguments), today)
    return analytics(user, today, filters)


# --------------------------------------------------------------------------
# Escrita
# --------------------------------------------------------------------------

def money_label(value):
    """Valor como o usuário lê, para o cartão de confirmação."""
    return f'R$ {number_format(value, decimal_pos=2, use_l10n=True)}'


def moment_label(moment):
    return date_format(timezone.localtime(moment), 'd/m/Y H:i')


def summarize(kind, form, arguments):
    """O que o cartão de confirmação mostra.

    Os rótulos são resolvidos aqui, e não no front, porque quem os tem em mãos é
    quem acabou de validar: o formulário devolve as instâncias em cleaned_data,
    e pedi-las de novo na tela seriam consultas para reescrever o que já se sabe.

    O que o cartão precisa mostrar sem falta é a data que ficará gravada. No
    crédito ela não é a data que o usuário falou — o formulário troca a data da
    compra pelo vencimento da fatura —, e é exatamente aí que uma confirmação
    silenciosa viraria um lançamento semanas fora do lugar.
    """
    data = form.cleaned_data
    rows = [('Valor', money_label(data['value']))]

    if kind == 'transaction':
        rows += [
            ('Tipo', dict(Type.choices)[data['type']]),
            ('Método', dict(Method.choices)[data['method']]),
            ('Conta', str(data['account'])),
        ]
        if data.get('card'):
            rows.append(('Cartão', str(data['card'])))
        # A natureza só aparece quando foge do comum: escrever "Regular" em
        # todo cartão treinaria o usuário a não ler a linha justamente onde
        # ela importa, que é num ajuste ou numa movimentação interna.
        if data.get('nature') and data['nature'] != Nature.REGULAR:
            rows.append(('Natureza', dict(Nature.choices)[data['nature']]))
    elif kind == 'installment':
        rows += [
            ('Parcelas', f'{data["installments"]}x'),
            ('Conta', str(data['account'])),
            ('Cartão', str(data['card'])),
        ]
    else:
        rows += [
            ('De', str(data['origin'])),
            ('Para', str(data['destination'])),
        ]

    rows.append(('Categoria', str(data['category']) if data.get('category') else 'Categoria Não Identificada'))
    if data.get('description'):
        rows.append(('Descrição', data['description']))

    note = None
    if kind == 'installment':
        moment = data['card'].invoice_datetime(data['datetime'], 0)
        rows.append(('1ª parcela vence em', moment_label(moment)))
        note = 'As demais parcelas caem no vencimento das faturas seguintes. A divisão do valor é feita pelo sistema.'
    else:
        rows.append(('Data', moment_label(data['datetime'])))
        # O formulário troca a data da compra pelo vencimento no crédito. Quando
        # isso aconteceu, o usuário precisa ver que a data mudou e por quê.
        informed = arguments.get('datetime')
        if kind == 'transaction' and data.get('card'):
            note = 'Compra no crédito: a data acima é o vencimento da fatura, não o dia da compra.'
            if informed:
                note = f'Compra no crédito feita em {informed}: a data acima é o vencimento da fatura em que ela entra.'

    return {
        'kind': kind,
        'label': KINDS[kind]['label'],
        'rows': [{'label': label, 'value': value} for label, value in rows],
        'note': note,
    }


def registrar_lancamento(user, today, arguments, conversation):
    """Valida o lançamento e o deixa à espera do usuário.

    O que sai daqui não é uma transação: é uma proposta gravada com os dados que
    já passaram pela validação. A confirmação reabre o mesmo formulário com o
    mesmo payload, então o que o usuário viu no cartão é o que será gravado — e
    não uma segunda interpretação do que ele disse no chat.
    """
    kind = arguments.get('kind', 'transaction')
    spec = KINDS.get(kind)
    if spec is None:
        return {
            'ok': False,
            'error': 'kind_desconhecido',
            'message': f'Tipo de lançamento desconhecido: {kind!r}. Use um de: {", ".join(KINDS)}.',
        }, None

    form_class = spec['form']
    accepted = set(form_class.base_fields)
    unknown = sorted(set(arguments) - accepted - {'kind'})

    payload = {key: value for key, value in arguments.items() if key in accepted and not is_filler(value)}

    # A data é resolvida agora, e não na confirmação: entre propor e clicar o
    # relógio anda, e um payload sem data gravaria o instante do clique enquanto
    # o cartão prometeu o da proposta.
    if not payload.get('datetime'):
        payload['datetime'] = timezone.localtime().replace(second=0, microsecond=0).isoformat()

    form = form_class(data=payload, user=user)

    if not form.is_valid():
        return {
            'ok': False,
            'error': 'validation_failed',
            'message': f'{spec["label"]} não montada: os dados violam alguma regra. Nada foi gravado nem proposto ao usuário.',
            'errors': {field: list(messages) for field, messages in form.errors.items()},
            'accepted_fields': sorted(accepted),
            'warnings': warnings_for(unknown, accepted),
        }, None

    summary = summarize(kind, form, arguments)

    pending = PendingWrite.objects.create(
        user=user,
        conversation=conversation,
        kind=kind,
        payload=payload,
        summary=summary,
    )

    return {
        'ok': True,
        'status': 'aguardando_confirmacao',
        'message': (
            f'{spec["label"]} montada e exibida ao usuário para confirmação. NADA foi gravado ainda: '
            f'o registro só acontece quando ele clicar em confirmar. Peça a confirmação e não afirme '
            f'que o lançamento existe. O usuário JÁ ESTÁ VENDO na tela, num cartão, tudo o que vem em '
            f'"resumo" — valor, tipo, método, conta, cartão, categoria, descrição, data e o aviso. '
            f'Não repita nenhum desses dados na sua resposta: escreva UMA frase curta pedindo a '
            f'confirmação no cartão. O "resumo" abaixo é só para você saber o que foi proposto, caso '
            f'o usuário pergunte depois.'
        ),
        'resumo': summary,
        'warnings': warnings_for(unknown, accepted),
    }, pending


def warnings_for(unknown, accepted):
    """Campo que o formulário não conhece vira aviso, não silêncio.

    Um modelo que errou o nome de um campo precisa saber que o valor não entrou,
    senão conta ao usuário algo que não vai ser gravado.
    """
    return [
        f'Campo ignorado por não existir neste lançamento: {name!r}. Campos aceitos: {", ".join(sorted(accepted))}.'
        for name in unknown
    ]


# --------------------------------------------------------------------------
# Despacho
# --------------------------------------------------------------------------

READERS = {
    'consultar_opcoes': consultar_opcoes,
    'consultar_financas': consultar_financas,
}


def run(name, arguments, *, user, today, conversation):
    """Executa a ferramenta e devolve (resposta_para_o_modelo, pendente_ou_None).

    Erro de filtro volta como resposta, e não como exceção: a mensagem diz o que
    o parâmetro aceita, e é o próprio modelo quem corrige e chama de novo. Fazer
    isso subir até a view derrubaria a conversa por um engano que se conserta na
    tentativa seguinte.
    """
    if name == 'registrar_lancamento':
        return registrar_lancamento(user, today, arguments, conversation)

    reader = READERS.get(name)
    if reader is None:
        return {'ok': False, 'error': 'ferramenta_desconhecida', 'message': f'Não existe ferramenta {name!r}.'}, None

    try:
        return reader(user, today, arguments), None
    except FilterError as error:
        return {'ok': False, 'error': 'filtro_invalido', 'message': str(error)}, None
