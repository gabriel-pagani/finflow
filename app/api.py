"""Endpoints JSON para agentes externos, como o do n8n.

A consulta é repartida por assunto, e não numa resposta única, porque o custo de
um agente de IA se mede em tokens por mensagem. As regras do sistema não mudam
entre uma pergunta e outra: quem as lê uma vez não precisa recebê-las de novo a
cada saldo consultado. Então cada rota carrega só um tipo de coisa:

    GET  /api/documentation/  as regras. Leitura única, no início da conversa.
    GET  /api/options/        o que existe para escolher num lançamento.
    GET  /api/analytics/      como o dinheiro está e como ele se moveu.
    POST /api/transactions/   registra transação avulsa, parcelamento ou transferência.

A análise é filtrável por período, conta, categoria, cartão, método, tipo,
natureza, origem, valor e descrição, e o agente escolhe também por quais eixos
quer ver os totais quebrados. Sem isso ele só saberia perguntar "os últimos N
meses" e teria de somar o resto na mão — que é onde ele erra, e caro.

A escrita não reimplementa regra nenhuma. Ela usa os mesmos formulários da
interface — TransactionForm, InstallmentForm, TransferForm —, então uma regra que
mude na tela muda aqui junto, e não há como a API aceitar o que a tela recusa. Foi
por isso que os três lançamentos entraram: são exatamente os três que o modal
"Nova Transação" oferece.

A rota /api/context/ continua existindo, devolvendo tudo de uma vez, para o
fluxo que ainda aponta para ela não quebrar no deploy. Ela é a composição das
outras, sem lógica própria: quem migrar para as rotas separadas pode esquecê-la.
"""

import json
import re
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.db.models import Count, DecimalField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek, TruncYear
from django.http import HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
import reversion

from .forms import InstallmentForm, TransactionForm, TransferForm
from .models import (
    Account,
    ApiToken,
    BusinessRule,
    Card,
    Category,
    Contribution,
    Installment,
    Investment,
    Method,
    Nature,
    Redemption,
    Transaction,
    Transfer,
    Type,
    Yield,
    add_months,
)


ZERO = Decimal('0.00')

# Métodos que o painel do realizado considera: dinheiro que já saiu da conta. O
# crédito fica de fora porque ainda vai vencer, e somá-lo ao saldo contaria duas
# vezes a mesma compra — uma agora, outra quando a fatura for paga. É o padrão da
# análise, não uma amarra: quem passar ?method= escolhe outro recorte.
SETTLED_METHODS = [Method.DEBIT, Method.NOT_APPLICABLE]

# Rótulos do que não tem registro do outro lado. Ficam aqui, e não espalhados
# pelas funções de agregação, porque o agente compara a string que recebe.
UNCATEGORIZED = 'Categoria Não Identificada'
NO_CARD = 'Sem Cartão'


# --------------------------------------------------------------------------
# Resposta
# --------------------------------------------------------------------------

def json_default(value):
    """Converte o que o json não conhece.

    Valor monetário vira string, não float: 0.1 + 0.2 em ponto flutuante não dá
    0.3, e um agente que receba 25.999999 vai repetir esse número de volta.
    """
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    raise TypeError(f'Sem conversão para JSON: {type(value).__name__}')


def json_response(payload, status=200, headers=None):
    response = HttpResponse(
        json.dumps(payload, default=json_default, ensure_ascii=False, indent=2),
        content_type='application/json; charset=utf-8',
        status=status,
    )
    for name, value in (headers or {}).items():
        response[name] = value
    return response


def money(value):
    """Total que veio de um Sum, já com o None do conjunto vazio resolvido."""
    return (value or ZERO).quantize(Decimal('0.01'))


# --------------------------------------------------------------------------
# Autenticação
# --------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class ApiView(View):
    """Base das views da API: autentica por token e responde sempre em JSON.

    O CSRF é dispensado porque não há sessão nem cookie em jogo. O que prova a
    identidade é o cabeçalho Authorization, e um site de terceiros não consegue
    fazer o navegador da vítima enviá-lo — que é justamente o ataque que o token
    de CSRF existe para barrar. Manter a checagem aqui só quebraria o n8n, que
    não tem de onde tirar o cookie.

    O usuário autenticado fica em request.api_user, e não em request.user: o
    AuthenticationMiddleware já preenche request.user com o AnonymousUser da
    sessão inexistente, e sobrescrevê-lo confundiria quem lesse a request
    esperando o comportamento normal do Django.
    """

    def unauthorized(self, message):
        # O WWW-Authenticate é o que diz ao cliente qual esquema usar; sem ele o
        # 401 fica mudo sobre como se autenticar.
        return json_response(
            {'ok': False, 'error': 'unauthorized', 'message': message},
            status=401,
            headers={'WWW-Authenticate': 'Bearer realm="finflow"'},
        )

    def dispatch(self, request, *args, **kwargs):
        header = request.META.get('HTTP_AUTHORIZATION', '')

        if not header:
            return self.unauthorized('Cabeçalho Authorization ausente. Use: Authorization: Bearer <token>.')

        scheme, _, raw = header.partition(' ')
        if scheme.lower() != 'bearer' or not raw.strip():
            return self.unauthorized('Cabeçalho Authorization malformado. Use: Authorization: Bearer <token>.')

        token = ApiToken.resolve(raw.strip())
        if token is None:
            return self.unauthorized('Token inválido ou revogado.')

        if not token.user.is_active:
            return json_response(
                {'ok': False, 'error': 'forbidden', 'message': 'O usuário desta credencial está inativo.'},
                status=403,
            )

        token.touch()
        request.api_token = token
        request.api_user = token.user

        return super().dispatch(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        # O padrão do Django devolve HTML; quem consome esta rota espera JSON
        # inclusive no erro, senão o n8n quebra ao tentar parsear a resposta.
        allowed = ', '.join(method.upper() for method in self.http_method_names if hasattr(self, method))
        return json_response(
            {'ok': False, 'error': 'method_not_allowed', 'message': f'Método não permitido. Aceito(s): {allowed}.'},
            status=405,
            headers={'Allow': allowed},
        )


# --------------------------------------------------------------------------
# Base de conhecimento
#
# Servida por /api/documentation/, em seções que o agente pode pedir avulsas.
# Um agente que nunca viu este sistema precisa aprender as regras antes de
# montar o primeiro lançamento: ele não tem como abrir o código, e errar aqui
# grava dinheiro errado. O que ele não precisa é reler tudo a cada mensagem, e é
# essa releitura que a rota separada evita.
# --------------------------------------------------------------------------

DOCUMENTATION = {
    'visao_geral': (
        'FinFlow é um sistema de finanças pessoais. Tudo o que existe nele desemboca em '
        'Transação: é ela que compõe saldo, entrada, saída e previsão. Algumas transações '
        'são avulsas, criadas diretamente; outras são derivadas, geradas por um registro de '
        'origem (parcelamento, transferência ou investimento). Transação derivada nunca é '
        'criada nem editada diretamente — mexe-se na origem, e ela regera as filhas.'
    ),

    'endpoints': {
        'GET /api/documentation/': (
            'Estas regras. Não mudam entre uma pergunta e outra: leia uma vez, no início da '
            'conversa, e não repita a chamada. Aceita ?sections=a,b para receber só parte '
            'delas; sem o parâmetro vêm todas.'
        ),
        'GET /api/options/': (
            'Contas, categorias, cartões e os códigos de tipo, método e natureza. É o que se '
            'consulta antes de gravar, para ter os ids e saber o que cada conta aceita. Muda '
            'só quando o usuário cadastra algo novo.'
        ),
        'GET /api/analytics/': (
            'Posição, totais, séries e transações, com filtro por período, conta, categoria, '
            'cartão, método, tipo, natureza, origem, valor e descrição. É a rota de toda '
            'pergunta sobre dinheiro. Veja a seção "analise".'
        ),
        'POST /api/transactions/': 'Registra transação avulsa, parcelamento ou transferência. Veja "como_registrar".',
        'GET /api/context/': (
            'Tudo o que as rotas acima devolvem, numa resposta só. Existe para o fluxo antigo '
            'não quebrar e é cara em tokens: prefira as rotas separadas.'
        ),
    },

    'como_registrar': {
        'endpoint': 'POST /api/transactions/',
        'discriminador': (
            'O campo "kind" escolhe o que será lançado, e é obrigatório. Os três valores '
            'correspondem exatamente às três opções do botão "Nova Transação" da interface.'
        ),
        'kinds': {
            'transaction': 'Lançamento único de entrada ou saída. Use para o caso comum: uma compra, um recebimento.',
            'installment': 'Compra no crédito dividida em parcelas mensais. Gera uma transação por parcela.',
            'transfer': 'Movimentação de valor entre duas contas do próprio usuário. Gera duas transações.',
        },
        'formatos': {
            'valor': (
                'Decimal em string ou número, com PONTO como separador decimal: "25.00", "1234.56". '
                'Vírgula é rejeitada ("1234,56" não é aceito). Não use separador de milhar. '
                'O valor deve ser sempre maior que zero: o que distingue entrada de saída é o campo '
                '"type", nunca o sinal do valor.'
            ),
            'data_hora': (
                'Aceita "2026-08-31T14:30", "2026-08-31 14:30", "2026-08-31T14:30:00-03:00" e o '
                'formato brasileiro "31/08/2026 14:30". Sem fuso explícito, entende-se '
                'America/Sao_Paulo. Se o campo for omitido, usa-se o instante atual — mesmo '
                'comportamento da tela, que já abre o campo preenchido com o agora.'
            ),
            'referencias': 'Conta, categoria, cartão e investimento são informados pelo "id" numérico devolvido em /api/options/.',
        },
    },

    'regras_de_negocio': (
        'Cada conta declara quais combinações de tipo e método aceita, e uma combinação não '
        'cadastrada é recusada. Isso existe para o cadastro refletir o mundo: uma conta de '
        'investimento não recebe compra no crédito, um cartão pré-pago não tem débito. '
        'Antes de montar qualquer lançamento, consulte accounts[].allowed_combinations em '
        '/api/options/: só o que estiver ali passa. Lançar fora disso retorna erro de '
        'validação, não grava nada.'
    ),

    'ciclo_do_cartao': {
        'resumo': (
            'ATENÇÃO — esta é a regra mais fácil de errar. Ao lançar no crédito, você informa a data '
            'da COMPRA, mas o que fica gravado na transação é a data de VENCIMENTO da fatura em que '
            'aquela compra caiu. Uma compra de hoje pode aparecer gravada semanas à frente. Isso é '
            'esperado: é o que faz a previsão de gastos bater com o que será efetivamente pago.'
        ),
        'como_a_fatura_e_escolhida': (
            'A compra entra na primeira fatura que ainda não fechou. Comprou ANTES do dia de '
            'fechamento, cai na fatura do mês corrente; comprou NO dia do fechamento ou depois, cai '
            'na do mês seguinte.'
        ),
        'fechamento_e_vencimento': (
            'Fechamento e vencimento são guardados como dia do mês, não como data, porque o ciclo se '
            'repete todo mês. O fechamento cai sempre no dia configurado, inclusive sábado e domingo: '
            'fechar é a operadora encerrar a fatura, e para isso não é preciso banco aberto. O '
            'vencimento, ao contrário, depende de banco aberto — se cair em fim de semana, anda para a '
            'segunda-feira seguinte. Feriado não entra na conta, porque o sistema não mantém calendário '
            'de feriados. Quando o dia de vencimento é menor ou igual ao de fechamento, o vencimento é '
            'do mês seguinte ao do fechamento. Num mês curto, um dia configurado além do fim do mês é '
            'limitado ao último dia dele: quem fecha dia 31 fecha dia 28 em fevereiro.'
        ),
        'exemplo': (
            'Cartão que fecha dia 20 e vence dia 27. Compra em 19/03: a fatura de março ainda não '
            'fechou, então a compra entra nela e é gravada em 27/03. Compra em 20/03 (o próprio dia do '
            'fechamento): já perdeu a fatura de março, entra na de abril e é gravada em 27/04. Compra '
            'em 25/03: idem, 27/04.'
        ),
        'campo_card': (
            'O cartão é obrigatório quando method="CREDIT" e ignorado nos demais métodos. É por ele '
            'que se descobre o ciclo. O cartão precisa ser do próprio usuário e da mesma conta '
            'informada no lançamento.'
        ),
        'efeito_na_analise': (
            'Como a transação do crédito é gravada na data de VENCIMENTO, uma análise por período '
            'agrupa a compra no mês em que ela será paga, não naquele em que foi feita. Perguntas '
            'sobre "quanto gastei no cartão em março" respondem-se com o vencimento de março.'
        ),
    },

    'parcelamento': {
        'o_que_e': 'Compra no crédito dividida em parcelas mensais. É sempre crédito, então o cartão é sempre obrigatório.',
        'valor': (
            'O campo "value" é o valor TOTAL da compra, não o da parcela. O sistema divide: cada '
            'parcela recebe o total dividido pelo número de parcelas, arredondado para baixo em dois '
            'decimais, e a ÚLTIMA parcela absorve a diferença que sobrou. Assim a soma das parcelas '
            'devolve exatamente o total, sem centavo perdido no arredondamento. '
            'Exemplo: 100.00 em 3x vira 33.33 + 33.33 + 33.34.'
        ),
        'minimo': 'São exigidas no mínimo 2 parcelas. Para 1 parcela, use kind="transaction".',
        'datas': (
            'A primeira parcela cai no vencimento da fatura em que a compra entrou, e cada parcela '
            'seguinte soma um mês sobre o ciclo, não sobre a data da parcela anterior — assim uma '
            'parcela não herda o empurrão de fim de semana que valia só para a outra.'
        ),
        'resultado': 'Gera uma transação por parcela, todas com type="OUT" e method="CREDIT", numeradas em "parcel".',
    },

    'transferencia': {
        'o_que_e': 'Movimentação de valor entre duas contas do próprio usuário. Não é receita nem despesa: o dinheiro só mudou de lugar.',
        'resultado': (
            'Gera DUAS transações, ambas com nature="INTERNAL": uma saída na conta de origem, com '
            'method="DEBIT", e uma entrada na conta de destino, com method="NOT_APPLICABLE". O destino '
            'não é débito porque classificá-lo assim o colocaria no mesmo balde de uma receita de '
            'verdade, inflando a entrada do período.'
        ),
        'exigencias': (
            'Origem e destino precisam ser contas diferentes. A origem precisa aceitar saída em Débito '
            'e o destino precisa aceitar entrada em Não Se Aplica — as duas regras, porque sem ambas a '
            'transferência gravaria metade e deixaria o saldo torto.'
        ),
    },

    'investimento': {
        'o_que_e': (
            'Uma posição investida (CDI, Tesouro Selic, LCI). Acumula aplicações, resgates e '
            'rendimentos, e o saldo é aplicado + rendido - resgatado.'
        ),
        'nao_editavel_pela_api': (
            'IMPORTANTE: investimento, aplicação, resgate e rendimento NÃO são criados por esta API '
            'nem pela interface — só pelo portal de administração. A consulta os expõe apenas para '
            'leitura e análise. Um agente não deve prometer ao usuário que registrou uma aplicação.'
        ),
        'efeito_em_transacao': (
            'Aplicação gera uma saída em Débito e resgate gera uma entrada em Não Se Aplica. '
            'Rendimento NÃO gera transação nenhuma: ele entra no saldo do investimento, mas não no '
            'saldo em conta, porque o dinheiro rendeu sem sair nem entrar em lugar nenhum.'
        ),
    },

    'naturezas': {
        'REGULAR': 'Movimento comum. É o padrão quando o campo é omitido, e o único que entra nas análises de entrada e saída.',
        'INTERNAL': 'Movimentação interna, aplicada automaticamente às duas pernas da transferência. Fica fora das análises para não contar o mesmo dinheiro dos dois lados.',
        'ADJUSTMENT': 'Ajuste de saldo, para corrigir divergência com o extrato real. Conta no saldo, mas fica fora das análises de entrada e saída.',
        'como_usar': 'Deixe em branco (ou "REGULAR") em praticamente todo lançamento. Use "ADJUSTMENT" apenas quando o objetivo for corrigir o saldo, não registrar um gasto ou receita real.',
    },

    'analise': {
        'endpoint': 'GET /api/analytics/',
        'como_funciona': (
            'Os filtros recortam um conjunto de transações; as seções pedidas em "include" descrevem '
            'esse mesmo conjunto de vários ângulos. Todo parâmetro que aceita lista recebe valores '
            'separados por vírgula. A resposta devolve em "filters" o recorte que realmente valeu, '
            'inclusive os padrões aplicados — confira ali antes de afirmar um número ao usuário.'
        ),
        'periodo': {
            'start / end': (
                'Recorte explícito, inclusivo nas duas pontas. Aceita "2026-03-15", "2026-03" (o mês '
                'inteiro: start vira o dia 1, end vira o último dia), "2026" (o ano inteiro) e '
                '"15/03/2026". Sem "end", vale hoje.'
            ),
            'months': (
                'Alternativa a "start": janela móvel de N meses cheios terminando no mês de "end". '
                'months=1 é o mês corrente, months=12 são os últimos doze. Ignorado se "start" vier.'
            ),
            'padrao': 'Sem nenhum dos três, analisam-se os últimos 12 meses.',
        },
        'filtros': {
            'account': 'Ids de conta, separados por vírgula. Ex.: account=1,3.',
            'category': 'Ids de categoria. Aceita o valor "null" para as transações sem categoria. Ex.: category=2,null.',
            'card': 'Ids de cartão. Aceita "null" para o que não passou em cartão.',
            'type': 'IN, OUT ou ambos. Sem o parâmetro, os dois entram.',
            'method': (
                'CREDIT, DEBIT, NOT_APPLICABLE. PADRÃO: DEBIT e NOT_APPLICABLE, o dinheiro que já saiu '
                'da conta. Para analisar o cartão passe method=CREDIT; para tudo, method=all.'
            ),
            'nature': (
                'REGULAR, INTERNAL, ADJUSTMENT. PADRÃO: REGULAR, senão transferência e ajuste inflariam '
                'entrada e saída dos dois lados. Para tudo, nature=all.'
            ),
            'origin': (
                'De onde a transação veio: standalone (avulsa), installment (parcela), transfer (perna '
                'de transferência) ou investment (aplicação/resgate). Ex.: origin=installment responde '
                '"quanto do meu gasto é parcelado".'
            ),
            'min_value / max_value': 'Faixa de valor de cada transação, não do total.',
            'search': 'Trecho contido na descrição, sem diferenciar maiúsculas.',
        },
        'group_by': (
            'Eixos pelos quais os totais são quebrados, separados por vírgula. Aceita: month, day, '
            'week, year, category, account, card, method, type, nature. PADRÃO: month,category. Cada '
            'eixo devolve linhas {key, label, income, outcome, net, count}, e as linhas de todos os '
            'eixos somam o mesmo total — são recortes do mesmo conjunto, não subconjuntos dele.'
        ),
        'include': (
            'Seções da resposta, separadas por vírgula. Aceita: summary (totais do recorte), '
            'breakdowns (as quebras de group_by), position (saldo e investimentos), forecast (crédito '
            'a vencer) e transactions (a lista em si). PADRÃO: summary,breakdowns,position. Peça só o '
            'que for usar: transactions é de longe a seção mais cara.'
        ),
        'controle_de_tamanho': {
            'top': 'Limita cada quebra não temporal às N maiores linhas, somando o resto numa linha "outros". Assim o total continua fechando.',
            'limit / offset': 'Tamanho e deslocamento da lista de transactions. limit=0 devolve a contagem sem as linhas.',
            'order': 'Ordem da lista: recent (padrão), oldest, largest ou smallest — largest responde "meus maiores gastos".',
        },
        'escopos_que_ignoram_o_filtro': (
            'position é sempre a posição acumulada inteira: saldo não tem período, e recortá-lo daria '
            'um número que não existe no extrato. forecast é sempre crédito a vencer daqui para a '
            'frente, mas obedece aos filtros de conta, categoria, cartão, valor e descrição. As duas '
            'seções trazem um campo "scope" repetindo isso.'
        ),
    },

    'como_ler_os_numeros': {
        'balance': 'Saldo em conta: posição acumulada, entradas menos saídas, considerando TODAS as naturezas e sem recorte de período. Só métodos liquidados (Débito e Não Se Aplica); o crédito fica fora porque ainda vai vencer.',
        'invested': 'Soma dos saldos dos investimentos (aplicado + rendido - resgatado). É posição, não fluxo.',
        'income / outcome': 'Entradas e saídas do recorte pedido. Nos padrões, só natureza REGULAR e só métodos liquidados.',
        'net': 'income - outcome do mesmo recorte. Positivo é sobra, negativo é rombo.',
        'count': 'Quantas transações compõem aquela linha. Serve para ver se um total é uma compra grande ou muitas pequenas.',
        'forecast': 'Gasto já assumido no crédito que ainda vai vencer: type="OUT", method="CREDIT", natureza REGULAR, daqui para a frente. Não se soma ao saldo — é o que sairá dele.',
        'cuidado': 'Não some balance com forecast nem income com o valor das transferências: são recortes diferentes e o mesmo dinheiro seria contado duas vezes.',
    },

    'erros': {
        '400': (
            'Requisição malformada. Na escrita: corpo que não é JSON válido, que não é um objeto, ou '
            '"kind" desconhecido. Na consulta: parâmetro de filtro inválido — o campo "message" diz '
            'qual é e o que ele aceita, e basta refazer a chamada corrigida.'
        ),
        '401': 'Token ausente, malformado, inválido ou revogado.',
        '403': 'Usuário da credencial inativo.',
        '405': 'Método HTTP errado para a rota.',
        '422': 'Os dados violam uma regra de negócio ou de validação. O corpo traz "errors", mapeando campo para a lista de mensagens; erros que não pertencem a um campo específico vêm em "__all__". Nada foi gravado.',
    },
}


# --------------------------------------------------------------------------
# Serialização
# --------------------------------------------------------------------------

def serialize_choices(choices):
    return [{'value': value, 'label': label} for value, label in choices]


def serialize_transaction(transaction):
    """Uma transação como o agente precisa vê-la, com rótulo ao lado do código.

    O código é o que se reenvia numa próxima chamada; o rótulo é o que se mostra
    a quem lê. Mandar só um dos dois obrigaria o agente a traduzir por conta
    própria, e é aí que ele inventa.
    """
    return {
        'id': transaction.id,
        'datetime': transaction.datetime,
        'account': {'id': transaction.account_id, 'description': str(transaction.account)},
        'card': {'id': transaction.card_id, 'description': str(transaction.card)} if transaction.card_id else None,
        'type': {'value': transaction.type, 'label': transaction.get_type_display()},
        'method': {'value': transaction.method, 'label': transaction.get_method_display()},
        'nature': {'value': transaction.nature, 'label': transaction.get_nature_display()},
        'category': {'id': transaction.category_id, 'description': transaction.category_display},
        'description': transaction.description,
        'value': transaction.value,
        'parcel': transaction.parcel,
        'origin': transaction.origin_display or None,
        'is_derived': transaction.is_derived,
    }


def serialize_card(card, today):
    """O cartão e o que o ciclo dele faria com uma compra feita hoje.

    As três datas calculadas evitam que o agente refaça a aritmética do ciclo —
    e erre. Elas respondem, já mastigado, "se eu lançar agora, quando vence?".
    """
    cycle = card.invoice_cycle(today)
    return {
        'id': card.id,
        'description': str(card),
        'account': {'id': card.account_id, 'description': str(card.account)},
        'last_digits': card.last_digits,
        'closing_day': card.closing_day,
        'due_day': card.due_day,
        'purchase_today': {
            'invoice_month': cycle.strftime('%Y-%m'),
            'closing_date': card.closing_date(cycle.year, cycle.month),
            'due_date': card.invoice_due_date(today),
            'explanation': (
                f'Uma compra lançada hoje ({today.isoformat()}) neste cartão entra na fatura de '
                f'{cycle.strftime("%m/%Y")} e será gravada com a data de vencimento '
                f'{card.invoice_due_date(today).isoformat()}.'
            ),
        },
    }


def investment_total(model):
    """Soma de um tipo de lançamento por investimento, como subconsulta.

    Somar aplicações, resgates e rendimentos numa annotate só produziria o
    produto cartesiano das três junções: cada aplicação apareceria uma vez por
    resgate, e os totais sairiam multiplicados. Uma subconsulta por relação
    mantém cada soma isolada — e continua sendo uma query só, ao contrário de
    percorrer os investimentos em Python.
    """
    return Coalesce(
        Subquery(
            model.objects.filter(investment=OuterRef('pk'))
            .values('investment')
            .annotate(total=Sum('value'))
            .values('total')[:1],
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
        ZERO,
    )


# --------------------------------------------------------------------------
# Filtros da análise
#
# Tudo o que chega pela querystring passa por aqui antes de virar queryset. Um
# parâmetro que o agente escreveu errado vira 400 com a mensagem do que se
# aceita, e não um filtro silenciosamente ignorado: o agente que não é avisado
# não corrige, e apresenta ao usuário um número de um recorte que ele não pediu.
# --------------------------------------------------------------------------

class FilterError(Exception):
    """Parâmetro de consulta que o cliente montou errado."""


# Valores que significam "sem registro do outro lado" nos filtros por id, e
# "não filtre por isto" nos filtros por código.
NULL_TOKENS = {'null', 'none', 'nenhum', 'nenhuma', 'sem'}
ALL_TOKENS = {'all', 'todos', 'todas', '*'}

DATE_FORMATS = ('%Y-%m-%d', '%d/%m/%Y')

# Origem da transação, como Q, espelhando Transaction.DERIVED_FIELDS. Responde
# "quanto do meu gasto é parcelado" sem o agente ter de cruzar listas.
ORIGINS = {
    'standalone': lambda: Q(**Transaction.standalone_filters()),
    'installment': lambda: Q(installment__isnull=False),
    'transfer': lambda: Q(transfer__isnull=False),
    'investment': lambda: Q(investment__isnull=False),
}

ORDERS = {
    'recent': ('-datetime', '-id'),
    'oldest': ('datetime', 'id'),
    'largest': ('-value', '-datetime'),
    'smallest': ('value', '-datetime'),
}


def read_list(params, name):
    """Parâmetro separado por vírgula, ou None quando não veio."""
    raw = params.get(name)
    if raw is None:
        return None
    values = [part.strip() for part in raw.split(',') if part.strip()]
    return values or None


def read_codes(params, name, allowed, default):
    """Lista de códigos aceitos, com "all" desligando o filtro.

    A comparação ignora caixa, mas o que volta é a grafia canônica: os códigos de
    transação são maiúsculos (DEBIT) e os de origem, minúsculos (installment), e
    quem recebe a lista a usa como chave.

    O default só vale para o parâmetro ausente. Quem escreve method= vazio está
    dizendo "não me dê o padrão", e recebe o conjunto inteiro.
    """
    values = read_list(params, name)
    if values is None:
        return list(default) if default is not None else None
    if any(value.lower() in ALL_TOKENS for value in values):
        return None

    canonical = {str(code).upper(): str(code) for code in allowed}

    codes = []
    for value in values:
        code = canonical.get(value.upper())
        if code is None:
            raise FilterError(
                f'Valor inválido em "{name}": {value!r}. Aceitos: {", ".join(canonical.values())} (ou "all").'
            )
        codes.append(code)
    return codes


def read_ids(params, name):
    """Ids numéricos e a marca de "sem este vínculo".

    Devolve (ids, inclui_vazio). O None de ids distingue "não filtre" de "filtre
    por lista vazia", que nunca casaria com nada.
    """
    values = read_list(params, name)
    if values is None:
        return None, False

    ids, include_null = [], False
    for value in values:
        if value.lower() in NULL_TOKENS:
            include_null = True
            continue
        if not value.isdigit():
            raise FilterError(f'Valor inválido em "{name}": {value!r}. Esperado um id numérico ou "null".')
        ids.append(int(value))
    return ids, include_null


def read_date(params, name, last_day):
    """Data do recorte, aceitando dia, mês, ano e o formato brasileiro.

    `last_day` decide para onde um mês ou ano incompleto se estica: o começo da
    janela vai para o primeiro dia, o fim para o último. Sem isso, end=2026-03
    cortaria março no dia 1.
    """
    raw = (params.get(name) or '').strip()
    if not raw:
        return None

    if re.fullmatch(r'\d{4}', raw):
        year = int(raw)
        return date(year, 12, 31) if last_day else date(year, 1, 1)

    if re.fullmatch(r'\d{4}-\d{1,2}', raw):
        year, month = (int(part) for part in raw.split('-'))
        if not 1 <= month <= 12:
            raise FilterError(f'Mês inválido em "{name}": {raw!r}.')
        return date(year, month, monthrange(year, month)[1] if last_day else 1)

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    raise FilterError(
        f'Data inválida em "{name}": {raw!r}. Aceitos: "2026-03-15", "2026-03", "2026" ou "15/03/2026".'
    )


def read_int(params, name, default, minimum, maximum):
    raw = (params.get(name) or '').strip()
    if not raw:
        return default
    if not raw.isdigit():
        raise FilterError(f'O parâmetro "{name}" espera um número inteiro. Recebido: {raw!r}.')

    value = int(raw)
    if not minimum <= value <= maximum:
        raise FilterError(f'O parâmetro "{name}" aceita de {minimum} a {maximum}. Recebido: {value}.')
    return value


def read_decimal(params, name):
    raw = (params.get(name) or '').strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise FilterError(
            f'O parâmetro "{name}" espera um decimal com ponto. Recebido: {raw!r}.'
        )


def read_choice(params, name, allowed, default):
    raw = (params.get(name) or '').strip()
    if not raw:
        return default
    if raw.lower() not in allowed:
        raise FilterError(f'Valor inválido em "{name}": {raw!r}. Aceitos: {", ".join(allowed)}.')
    return raw.lower()


class AnalyticsFilters:
    """O recorte pedido pela querystring, já validado e pronto para virar query.

    Guarda também de onde cada valor veio: os padrões desta API não são óbvios
    (só métodos liquidados, só natureza normal) e o agente precisa saber que eles
    entraram, ou vai narrar como "todos os gastos" um número que exclui o cartão.
    """

    DEFAULT_MONTHS = 12
    MAX_MONTHS = 120

    DEFAULT_LIMIT = 25
    MAX_LIMIT = 500
    MAX_OFFSET = 100000
    MAX_TOP = 200

    DEFAULT_GROUP_BY = ('month', 'category')
    DEFAULT_INCLUDE = ('summary', 'breakdowns', 'position')
    ALL_INCLUDES = ('summary', 'breakdowns', 'position', 'forecast', 'transactions')

    DEFAULT_FORECAST_MONTHS = 12
    MAX_FORECAST_MONTHS = 60

    def __init__(self, params, today, default_include=None, default_limit=None):
        self.today = today
        self.defaulted = []

        self.end = self.read_end(params, today)
        self.start, self.months = self.read_start(params)
        if self.start > self.end:
            raise FilterError(
                f'O início do período ({self.start.isoformat()}) é depois do fim ({self.end.isoformat()}).'
            )

        self.types = read_codes(params, 'type', Type.values, None)
        self.methods = read_codes(params, 'method', Method.values, SETTLED_METHODS)
        self.natures = read_codes(params, 'nature', Nature.values, [Nature.REGULAR])
        self.note_default(params, 'method', 'nature')

        self.account_ids, _ = read_ids(params, 'account')
        self.category_ids, self.uncategorized = read_ids(params, 'category')
        self.card_ids, self.without_card = read_ids(params, 'card')
        self.origins = read_codes(params, 'origin', tuple(ORIGINS), None)

        self.min_value = read_decimal(params, 'min_value')
        self.max_value = read_decimal(params, 'max_value')
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise FilterError('"min_value" é maior que "max_value": nenhuma transação caberia na faixa.')

        self.search = (params.get('search') or '').strip() or None

        self.group_by = self.read_group_by(params)
        self.include = self.read_include(params, default_include or self.DEFAULT_INCLUDE)
        self.note_default(params, 'group_by', 'include')

        self.top = read_int(params, 'top', 0, 0, self.MAX_TOP) or None
        # O default_limit pode ser zero, e zero é um pedido legítimo: "conte as
        # transações, não as liste". Por isso a comparação com None.
        fallback = self.DEFAULT_LIMIT if default_limit is None else default_limit
        self.limit = read_int(params, 'limit', fallback, 0, self.MAX_LIMIT)
        self.offset = read_int(params, 'offset', 0, 0, self.MAX_OFFSET)
        self.order = read_choice(params, 'order', tuple(ORDERS), 'recent')
        self.forecast_months = read_int(
            params, 'forecast_months', self.DEFAULT_FORECAST_MONTHS, 1, self.MAX_FORECAST_MONTHS
        )

    # -- leitura ----------------------------------------------------------

    def read_end(self, params, today):
        return read_date(params, 'end', last_day=True) or today

    def read_start(self, params):
        """O início da janela, e quantos meses ela cobre quando foi assim que veio.

        Recortar por dia deixaria o mês mais antigo pela metade e a série
        começaria com um degrau falso; por isso a janela por meses sempre abre no
        primeiro dia do mês.
        """
        start = read_date(params, 'start', last_day=False)
        if start is not None:
            return start, None

        months = read_int(params, 'months', self.DEFAULT_MONTHS, 1, self.MAX_MONTHS)
        if 'months' not in params:
            self.defaulted.append('months')
        return add_months(self.end.replace(day=1), -(months - 1)), months

    def read_group_by(self, params):
        values = read_list(params, 'group_by') or list(self.DEFAULT_GROUP_BY)
        for value in values:
            if value.lower() not in GROUPINGS:
                raise FilterError(
                    f'Eixo desconhecido em "group_by": {value!r}. Aceitos: {", ".join(GROUPINGS)}.'
                )
        # dict.fromkeys em vez de set: a ordem pedida é a ordem devolvida, e
        # repetir um eixo não deve repetir a quebra.
        return list(dict.fromkeys(value.lower() for value in values))

    def read_include(self, params, default):
        values = read_list(params, 'include')
        if values is None:
            return list(default)
        if any(value.lower() in ALL_TOKENS for value in values):
            return list(self.ALL_INCLUDES)

        for value in values:
            if value.lower() not in self.ALL_INCLUDES:
                raise FilterError(
                    f'Seção desconhecida em "include": {value!r}. Aceitas: {", ".join(self.ALL_INCLUDES)}.'
                )
        return list(dict.fromkeys(value.lower() for value in values))

    def note_default(self, params, *names):
        """Registra os filtros que o cliente não mandou e receberam padrão."""
        self.defaulted.extend(name for name in names if name not in params)

    # -- consulta ---------------------------------------------------------

    def dimensional_q(self):
        """Os filtros que dizem "de qual pedaço do cadastro", sem período nem medida.

        Separados dos demais porque a previsão os aceita — faz sentido prever o
        crédito de uma conta ou categoria — mas não aceita o recorte de período,
        de tipo, de método nem de natureza, que ela define por conta própria.
        """
        query = Q()

        if self.account_ids is not None:
            query &= Q(account_id__in=self.account_ids)

        if self.category_ids is not None:
            categories = Q(category_id__in=self.category_ids)
            if self.uncategorized:
                categories |= Q(category__isnull=True)
            query &= categories

        if self.card_ids is not None:
            cards = Q(card_id__in=self.card_ids)
            if self.without_card:
                cards |= Q(card__isnull=True)
            query &= cards

        if self.min_value is not None:
            query &= Q(value__gte=self.min_value)
        if self.max_value is not None:
            query &= Q(value__lte=self.max_value)

        if self.search:
            query &= Q(description__icontains=self.search)

        if self.origins is not None:
            origins = Q()
            for name in self.origins:
                origins |= ORIGINS[name]()
            query &= origins

        return query

    def queryset(self, user):
        """As transações do recorte inteiro: período, medidas e dimensões."""
        queryset = Transaction.objects.filter(user=user).filter(
            datetime__date__gte=self.start,
            datetime__date__lte=self.end,
        )

        if self.types is not None:
            queryset = queryset.filter(type__in=self.types)
        if self.methods is not None:
            queryset = queryset.filter(method__in=self.methods)
        if self.natures is not None:
            queryset = queryset.filter(nature__in=self.natures)

        return queryset.filter(self.dimensional_q())

    def transactions(self, queryset):
        """A fatia listada da consulta, na ordem pedida."""
        if not self.limit:
            return []
        page = queryset.order_by(*ORDERS[self.order])[self.offset:self.offset + self.limit]
        return [
            serialize_transaction(transaction)
            for transaction in page.select_related('account', 'card__account', 'category')
        ]

    # -- eco --------------------------------------------------------------

    def describe(self):
        """O recorte que realmente valeu, para o agente conferir antes de afirmar.

        Período, medidas e eixos aparecem sempre, mesmo vindos de padrão: são
        eles que mudam o sentido de um total. Os filtros de dimensão só aparecem
        quando foram pedidos, senão a resposta gastaria tokens repetindo nulos.
        """
        applied = {
            'period': {'start': self.start, 'end': self.end},
            'type': self.types or 'todos',
            'method': self.methods or 'todos',
            'nature': self.natures or 'todas',
            'group_by': self.group_by,
            'include': self.include,
        }
        if self.months is not None:
            applied['period']['months'] = self.months
        if self.defaulted:
            applied['defaulted'] = sorted(set(self.defaulted))

        optional = {
            'account': self.account_ids,
            'category': self.category_ids,
            'card': self.card_ids,
            'origin': self.origins,
            'min_value': self.min_value,
            'max_value': self.max_value,
            'search': self.search,
        }
        applied.update({name: value for name, value in optional.items() if value})

        if self.uncategorized:
            applied['category_includes_null'] = True
        if self.without_card:
            applied['card_includes_null'] = True
        if 'transactions' in self.include:
            applied['transactions'] = {'limit': self.limit, 'offset': self.offset, 'order': self.order}

        return applied


# --------------------------------------------------------------------------
# Agregação
#
# Todo eixo devolve linhas da mesma forma — {key, label, income, outcome, net,
# count} —, e é isso que deixa o agente aprender a ler uma quebra e saber ler as
# outras. Quebrar por mês e quebrar por categoria são recortes do mesmo conjunto:
# as linhas de cada eixo somam sempre o mesmo total.
# --------------------------------------------------------------------------

def month_grouping(truncate, key_format, label_format):
    return {
        'annotate': {'bucket': truncate('datetime')},
        'fields': ('bucket',),
        'key': lambda row: row['bucket'].strftime(key_format),
        'label': lambda row: row['bucket'].strftime(label_format),
        'chronological': True,
    }


GROUPINGS = {
    'month': month_grouping(TruncMonth, '%Y-%m', '%m/%Y'),
    'day': month_grouping(TruncDay, '%Y-%m-%d', '%d/%m/%Y'),
    'week': month_grouping(TruncWeek, '%Y-%m-%d', 'Semana de %d/%m/%Y'),
    'year': month_grouping(TruncYear, '%Y', '%Y'),
    'category': {
        'fields': ('category_id', 'category__description'),
        'key': lambda row: row['category_id'],
        'label': lambda row: row['category__description'] or UNCATEGORIZED,
    },
    'account': {
        'fields': ('account_id', 'account__description'),
        'key': lambda row: row['account_id'],
        'label': lambda row: row['account__description'],
    },
    'card': {
        'fields': ('card_id', 'card__last_digits', 'card__account__description'),
        'key': lambda row: row['card_id'],
        'label': lambda row: (
            f'{row["card__account__description"]} (final {row["card__last_digits"]})'
            if row['card_id'] else NO_CARD
        ),
    },
    'method': {
        'fields': ('method',),
        'key': lambda row: row['method'],
        'label': lambda row: Method(row['method']).label,
    },
    'type': {
        'fields': ('type',),
        'key': lambda row: row['type'],
        'label': lambda row: Type(row['type']).label,
    },
    'nature': {
        'fields': ('nature',),
        'key': lambda row: row['nature'],
        'label': lambda row: Nature(row['nature']).label,
    },
}


def serialize_row(entry):
    return {
        'key': entry['key'],
        'label': entry['label'],
        'income': money(entry['income']),
        'outcome': money(entry['outcome']),
        'net': money(entry['income'] - entry['outcome']),
        'count': entry['count'],
    }


def collapse(entries, top):
    """Corta a cauda da quebra numa linha "outros", em vez de descartá-la.

    Descartar economizaria os mesmos tokens, mas as linhas deixariam de somar o
    total do recorte — e um agente que confere a soma e não fecha ou desiste da
    resposta ou inventa a diferença.
    """
    head, tail = entries[:top], entries[top:]
    others = {
        'key': 'others',
        'label': f'Outros ({len(tail)})',
        'income': sum((entry['income'] for entry in tail), ZERO),
        'outcome': sum((entry['outcome'] for entry in tail), ZERO),
        'count': sum(entry['count'] for entry in tail),
    }
    return head + [others]


def breakdown(queryset, name, top=None):
    """Totais do recorte quebrados por um eixo.

    O order_by() vazio é obrigatório: a ordenação padrão do modelo entraria no
    GROUP BY e a agregação sairia repartida por datetime, uma linha por
    transação. Só a ordenação por agregado, aplicada depois em Python, é segura.
    """
    spec = GROUPINGS[name]

    rows = queryset.order_by()
    if 'annotate' in spec:
        rows = rows.annotate(**spec['annotate'])
    rows = rows.values(*spec['fields'], 'type').annotate(total=Sum('value'), count=Count('id'))

    grouped = {}
    for row in rows:
        key = spec['key'](row)
        entry = grouped.setdefault(
            key, {'key': key, 'label': spec['label'](row), 'income': ZERO, 'outcome': ZERO, 'count': 0}
        )
        entry['income' if row['type'] == Type.IN else 'outcome'] += row['total']
        entry['count'] += row['count']

    entries = list(grouped.values())
    if spec.get('chronological'):
        entries.sort(key=lambda entry: entry['key'])
    else:
        # Movimento total, e não só saída: numa consulta filtrada por entradas a
        # ordenação por saída deixaria todas as linhas empatadas em zero.
        entries.sort(key=lambda entry: entry['income'] + entry['outcome'], reverse=True)
        if top and len(entries) > top:
            entries = collapse(entries, top)

    return [serialize_row(entry) for entry in entries]


def summarize(queryset):
    """Os totais do recorte inteiro, sem quebra."""
    rows = {
        row['type']: row
        for row in queryset.order_by().values('type').annotate(total=Sum('value'), count=Count('id'))
    }

    income = money(rows.get(Type.IN, {}).get('total'))
    outcome = money(rows.get(Type.OUT, {}).get('total'))

    return {
        'income': income,
        'outcome': outcome,
        'net': money(income - outcome),
        'transactions': sum(row['count'] for row in rows.values()),
    }


# --------------------------------------------------------------------------
# Seções da consulta
# --------------------------------------------------------------------------

def options(user, today):
    """Tudo que pode ser escolhido num lançamento, com o que cada conta aceita.

    Conta e categoria são cadastros globais, sem dono: todo usuário enxerga os
    mesmos. Cartão tem dono, e por isso é filtrado.
    """
    allowed = {}
    for rule in BusinessRule.objects.all():
        allowed.setdefault(rule.account_id, []).append({
            'type': rule.type,
            'method': rule.method,
            'label': f'{Type(rule.type).label} em {Method(rule.method).label}',
        })

    cards = Card.objects.filter(user=user).select_related('account')
    cards_by_account = {}
    for card in cards:
        cards_by_account.setdefault(card.account_id, []).append(card.id)

    return {
        'accounts': [
            {
                'id': account.id,
                'description': account.description,
                'allowed_combinations': allowed.get(account.id, []),
                'card_ids': cards_by_account.get(account.id, []),
            }
            for account in Account.objects.all()
        ],
        'categories': [
            {'id': category.id, 'description': category.description}
            for category in Category.objects.all()
        ],
        'cards': [serialize_card(card, today) for card in cards],
        'types': serialize_choices(Type.choices),
        'methods': serialize_choices(Method.choices),
        'natures': serialize_choices(Nature.choices),
    }


POSITION_SCOPE = (
    'Posição acumulada: todas as naturezas, sem recorte de período e sem os filtros da consulta. '
    'Só métodos liquidados — o crédito ainda vai vencer e entra em "forecast".'
)


def position(user):
    """Onde o dinheiro está agora: saldo por conta e posição investida.

    Posição acumulada ignora recorte de período e conta todas as naturezas — é
    para isso que interna e ajuste existem. Filtrá-la pelo recorte da análise
    devolveria um saldo que não existe em extrato nenhum.
    """
    settled = Transaction.objects.filter(user=user, method__in=SETTLED_METHODS)

    by_account = {}
    for row in settled.order_by().values('account_id', 'account__description', 'type').annotate(total=Sum('value')):
        entry = by_account.setdefault(
            row['account_id'],
            {'id': row['account_id'], 'description': row['account__description'], 'income': ZERO, 'outcome': ZERO},
        )
        entry['income' if row['type'] == Type.IN else 'outcome'] += row['total']

    accounts = [{**entry, 'balance': money(entry['income'] - entry['outcome'])} for entry in by_account.values()]
    accounts.sort(key=lambda item: item['description'])

    investments = (
        Investment.objects.filter(user=user)
        .select_related('account', 'category')
        .annotate(
            applied=investment_total(Contribution),
            redeemed=investment_total(Redemption),
            yielded=investment_total(Yield),
        )
    )

    serialized_investments = []
    invested_total = ZERO
    for investment in investments:
        balance = investment.applied + investment.yielded - investment.redeemed
        invested_total += balance
        serialized_investments.append({
            'id': investment.id,
            'description': investment.description,
            'account': {'id': investment.account_id, 'description': str(investment.account)},
            'category': {'id': investment.category_id, 'description': investment.category_display},
            'applied': money(investment.applied),
            'yielded': money(investment.yielded),
            'redeemed': money(investment.redeemed),
            'balance': money(balance),
        })

    balance = sum((account['balance'] for account in accounts), ZERO)

    return {
        'scope': POSITION_SCOPE,
        'balance': money(balance),
        'invested': money(invested_total),
        'total': money(balance + invested_total),
        'accounts': accounts,
        'investments': serialized_investments,
    }


def forecast(user, today, filters):
    """Gasto no crédito já assumido que ainda vai vencer.

    Obedece aos filtros de dimensão — prever o cartão de uma conta ou categoria é
    pergunta legítima — mas não ao período nem às medidas: previsão é, por
    definição, saída em crédito daqui para a frente, e aceitar outro recorte
    devolveria sob o nome de previsão alguma outra coisa.
    """
    horizon = add_months(today, filters.forecast_months)
    window = Transaction.objects.filter(
        user=user,
        method=Method.CREDIT,
        type=Type.OUT,
        nature=Nature.REGULAR,
        datetime__date__gte=today,
        datetime__date__lte=horizon,
    ).filter(filters.dimensional_q())

    return {
        'scope': (
            f'Saída em crédito, natureza normal, com vencimento entre {today.isoformat()} e '
            f'{horizon.isoformat()}. Não se soma ao saldo: é o que sairá dele.'
        ),
        'total': money(window.aggregate(total=Sum('value'))['total']),
        'breakdowns': {name: breakdown(window, name, filters.top) for name in filters.group_by},
    }


def analytics(user, today, filters):
    """As seções pedidas em include, montadas sobre o mesmo recorte."""
    queryset = filters.queryset(user)
    payload = {'filters': filters.describe()}

    if 'summary' in filters.include:
        payload['summary'] = summarize(queryset)
    if 'breakdowns' in filters.include:
        payload['breakdowns'] = {name: breakdown(queryset, name, filters.top) for name in filters.group_by}
    if 'position' in filters.include:
        payload['position'] = position(user)
    if 'forecast' in filters.include:
        payload['forecast'] = forecast(user, today, filters)
    if 'transactions' in filters.include:
        payload['transactions'] = filters.transactions(queryset)

    return payload


# --------------------------------------------------------------------------
# Consulta
# --------------------------------------------------------------------------

class ReadView(ApiView):
    """Base das rotas de leitura: só GET, e o carimbo de quando a foto foi tirada.

    O generated_at não é enfeite: o agente costuma reaproveitar uma resposta ao
    longo da conversa, e sem a hora ele não tem como saber que está falando de
    um saldo de meia hora atrás.
    """

    http_method_names = ['get']

    def envelope(self, payload):
        return json_response({
            'ok': True,
            'generated_at': timezone.localtime(),
            **payload,
        })

    def invalid_filter(self, error):
        return json_response(
            {'ok': False, 'error': 'invalid_filter', 'message': str(error)},
            status=400,
        )


class IndexView(ReadView):
    """Índice das rotas. Barato de ler e evita o agente chutar caminho."""

    def get(self, request, *args, **kwargs):
        return self.envelope({
            'user': {'id': request.api_user.id, 'username': request.api_user.username},
            'endpoints': DOCUMENTATION['endpoints'],
        })


class DocumentationView(ReadView):
    """As regras do sistema, em seções.

    Rota separada porque a documentação é a maior parte da resposta e a única que
    não muda: paga-se uma vez, no início da conversa, em vez de a cada pergunta.
    """

    def get(self, request, *args, **kwargs):
        sections = read_list(request.GET, 'sections')

        if sections is None:
            return self.envelope({'documentation': DOCUMENTATION})

        unknown = [name for name in sections if name not in DOCUMENTATION]
        if unknown:
            return self.invalid_filter(FilterError(
                f'Seção desconhecida em "sections": {unknown[0]!r}. Aceitas: {", ".join(DOCUMENTATION)}.'
            ))

        return self.envelope({
            'sections': list(DOCUMENTATION),
            'documentation': {name: DOCUMENTATION[name] for name in sections},
        })


class OptionsView(ReadView):
    """O que existe para escolher num lançamento: ids, cadastros e códigos."""

    def get(self, request, *args, **kwargs):
        return self.envelope({'options': options(request.api_user, timezone.localdate())})


class AnalyticsView(ReadView):
    """Como o dinheiro está e como ele se moveu, no recorte que se pedir.

    Uma rota só para toda pergunta sobre dinheiro, e não uma por relatório,
    porque as perguntas de quem analisa não param de surgir: filtrar e escolher
    os eixos cobre as combinações sem uma rota nova a cada uma.
    """

    def get(self, request, *args, **kwargs):
        today = timezone.localdate()
        try:
            filters = AnalyticsFilters(request.GET, today)
        except FilterError as error:
            return self.invalid_filter(error)

        return self.envelope(analytics(request.api_user, today, filters))


class ContextView(ReadView):
    """Retrato completo numa resposta só: documentação, opções e análise.

    Mantida para o fluxo que já apontava para cá continuar de pé, e é a
    composição das outras rotas, sem lógica própria. Quem migrar para elas para
    de pagar a documentação inteira a cada pergunta, que é o custo desta aqui.
    """

    def get(self, request, *args, **kwargs):
        today = timezone.localdate()
        try:
            filters = AnalyticsFilters(
                request.GET,
                today,
                default_include=AnalyticsFilters.ALL_INCLUDES,
                # O nome antigo do limite da lista, aceito para a querystring que
                # já rodava em produção não mudar de sentido no deploy.
                default_limit=read_int(request.GET, 'transactions', AnalyticsFilters.DEFAULT_LIMIT, 0, AnalyticsFilters.MAX_LIMIT),
            )
        except FilterError as error:
            return self.invalid_filter(error)

        return self.envelope({
            'user': {'id': request.api_user.id, 'username': request.api_user.username},
            'documentation': DOCUMENTATION,
            'options': options(request.api_user, today),
            **analytics(request.api_user, today, filters),
        })


# --------------------------------------------------------------------------
# Escrita
# --------------------------------------------------------------------------

class TransactionCreateView(ApiView):
    """Registra transação avulsa, parcelamento ou transferência.

    Cada tipo é despachado para o formulário que a interface usa, sem regra
    reescrita: é o mesmo clean(), a mesma checagem de regra de negócio, o mesmo
    cálculo de fatura. O que a tela recusa, esta rota recusa com a mesma frase.
    """

    http_method_names = ['post']

    KINDS = {
        'transaction': {
            'form': TransactionForm,
            'model': Transaction,
            'label': 'Transação',
            'message': 'Transação criada com sucesso.',
        },
        'installment': {
            'form': InstallmentForm,
            'model': Installment,
            'label': 'Parcelamento',
            'message': 'Parcelamento criado com sucesso.',
        },
        'transfer': {
            'form': TransferForm,
            'model': Transfer,
            'label': 'Transferência',
            'message': 'Transferência criada com sucesso.',
        },
    }

    def bad_request(self, message):
        return json_response({'ok': False, 'error': 'bad_request', 'message': message}, status=400)

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body or b'{}')
        except json.JSONDecodeError as error:
            return self.bad_request(f'Corpo não é JSON válido: {error}.')

        if not isinstance(payload, dict):
            return self.bad_request('O corpo deve ser um objeto JSON.')

        kind = payload.pop('kind', 'transaction')
        spec = self.KINDS.get(kind)
        if spec is None:
            return self.bad_request(
                f'Tipo de lançamento desconhecido: {kind!r}. Use um de: {", ".join(self.KINDS)}.'
            )

        form_class = spec['form']

        # O campo de data já chega preenchido com o agora na tela; omiti-lo aqui
        # tem de significar a mesma coisa, e não um erro de obrigatoriedade.
        if not payload.get('datetime'):
            payload['datetime'] = timezone.localtime().replace(second=0, microsecond=0)

        # Chave que o formulário não conhece é devolvida como aviso, não ignorada
        # em silêncio: um agente que erra o nome de um campo precisa saber que o
        # valor não foi gravado, senão informa ao usuário algo que não aconteceu.
        accepted = set(form_class.base_fields)
        unknown = sorted(set(payload) - accepted)

        form = form_class(data={key: value for key, value in payload.items() if key in accepted}, user=request.api_user)

        if not form.is_valid():
            return json_response({
                'ok': False,
                'error': 'validation_failed',
                'message': f'{spec["label"]} não registrada: os dados enviados violam alguma regra.',
                'errors': {field: list(messages) for field, messages in form.errors.items()},
                'accepted_fields': sorted(accepted),
                'warnings': self.warnings(unknown, accepted),
            }, status=422)

        # A gravação inteira numa transação de banco: parcelamento e
        # transferência geram filhas no save, e uma falha no meio deixaria o
        # registro de origem sem as transações que o justificam.
        with db_transaction.atomic():
            with reversion.create_revision():
                reversion.set_user(request.api_user)
                reversion.set_comment(f'Criado pela API ({request.api_token.description}).')
                instance = form.save()

            # Carimba a credencial em tudo o que o lançamento gerou — a avulsa, as
            # parcelas ou as duas pernas —, para o admin poder distinguir depois o
            # que entrou pela API do que veio da tela. É update() de propósito:
            # não passa por save() nem regera transação, só grava a coluna.
            self.resulting_transactions(kind, instance).update(api_token=request.api_token)

        return json_response({
            'ok': True,
            'kind': kind,
            'message': spec['message'],
            'created': {'id': instance.id, 'label': str(instance)},
            'transactions': [serialize_transaction(t) for t in self.resulting_transactions(kind, instance)],
            'warnings': self.warnings(unknown, accepted),
        }, status=201)

    def warnings(self, unknown, accepted):
        if not unknown:
            return []
        return [
            f'Campo ignorado por não existir neste lançamento: {name!r}. '
            f'Campos aceitos: {", ".join(sorted(accepted))}.'
            for name in unknown
        ]

    def resulting_transactions(self, kind, instance):
        """As transações que o lançamento produziu.

        Devolvê-las é o que fecha o ciclo do crédito: é aqui que o agente vê a
        data de vencimento que a compra recebeu, em vez de supor que ela ficou
        no dia em que foi informada.
        """
        if kind == 'transaction':
            queryset = Transaction.objects.filter(pk=instance.pk)
        else:
            queryset = instance.transactions.all()
        return queryset.select_related('account', 'card__account', 'category').order_by('datetime', 'id')
