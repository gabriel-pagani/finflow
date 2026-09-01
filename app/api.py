"""Endpoints JSON para agentes externos, como o do n8n.

São dois. O de consulta descreve o sistema inteiro numa resposta só: o que existe
para escolher, como o dinheiro está hoje e as regras que governam cada lançamento.
O de escrita registra transação avulsa, parcelamento ou transferência.

A escrita não reimplementa regra nenhuma. Ela usa os mesmos formulários da
interface — TransactionForm, InstallmentForm, TransferForm —, então uma regra que
mude na tela muda aqui junto, e não há como a API aceitar o que a tela recusa. Foi
por isso que os três lançamentos entraram: são exatamente os três que o modal
"Nova Transação" oferece.

A consulta carrega a própria documentação. Um agente de IA não tem como ler este
código antes de montar um lançamento, e as regras daqui não são adivinháveis: que
a data do crédito vira o vencimento da fatura, que a transferência nasce em duas
pernas, que cada conta só aceita as combinações cadastradas. Tudo isso viaja junto
da resposta, para o agente ter a regra na mesma leitura em que tem as opções.
"""

import json
from datetime import timedelta
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce, TruncMonth
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
)


ZERO = Decimal('0.00')

# Métodos que o painel do realizado considera: dinheiro que já saiu da conta. O
# crédito fica de fora porque ainda vai vencer, e somá-lo ao saldo contaria duas
# vezes a mesma compra — uma agora, outra quando a fatura for paga.
SETTLED_METHODS = [Method.DEBIT, Method.NOT_APPLICABLE]


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
# Vai inteira dentro da resposta de consulta. Um agente que nunca viu este
# sistema precisa aprender as regras na mesma leitura em que recebe as opções:
# ele não tem como abrir o código, e errar aqui grava dinheiro errado.
# --------------------------------------------------------------------------

DOCUMENTATION = {
    'visao_geral': (
        'FinFlow é um sistema de finanças pessoais. Tudo o que existe nele desemboca em '
        'Transação: é ela que compõe saldo, entrada, saída e previsão. Algumas transações '
        'são avulsas, criadas diretamente; outras são derivadas, geradas por um registro de '
        'origem (parcelamento, transferência ou investimento). Transação derivada nunca é '
        'criada nem editada diretamente — mexe-se na origem, e ela regera as filhas.'
    ),

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
            'referencias': 'Conta, categoria, cartão e investimento são informados pelo "id" numérico devolvido na consulta.',
        },
    },

    'regras_de_negocio': (
        'Cada conta declara quais combinações de tipo e método aceita, e uma combinação não '
        'cadastrada é recusada. Isso existe para o cadastro refletir o mundo: uma conta de '
        'investimento não recebe compra no crédito, um cartão pré-pago não tem débito. '
        'Antes de montar qualquer lançamento, consulte options.accounts[].allowed_combinations: '
        'só o que estiver ali passa. Lançar fora disso retorna erro de validação, não grava nada.'
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
            'nem pela interface — só pelo portal de administração. Esta consulta os expõe apenas para '
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

    'como_ler_os_numeros': {
        'balance': 'Saldo em conta: posição acumulada, entradas menos saídas, considerando TODAS as naturezas e sem recorte de período. Só métodos liquidados (Débito e Não Se Aplica); o crédito fica fora porque ainda vai vencer.',
        'invested': 'Soma dos saldos dos investimentos (aplicado + rendido - resgatado). É posição, não fluxo.',
        'income / outcome': 'Entradas e saídas do período consultado, só natureza REGULAR e só métodos liquidados. Interna e ajuste ficam fora de propósito.',
        'forecast': 'Gasto já assumido no crédito que ainda vai vencer: type="OUT", method="CREDIT", natureza REGULAR, daqui para a frente. Não se soma ao saldo — é o que sairá dele.',
        'cuidado': 'Não some balance com forecast nem income com o valor das transferências: são recortes diferentes e o mesmo dinheiro seria contado duas vezes.',
    },

    'erros': {
        '400': 'Corpo malformado: não é JSON válido, não é um objeto, ou o "kind" é desconhecido.',
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
# Consulta
# --------------------------------------------------------------------------

class ContextView(ApiView):
    """Retrato completo do sistema para o agente: opções, posição e regras.

    Vai tudo numa resposta só, e não em rotas separadas, porque o agente precisa
    das três coisas na mesma volta: sem as opções ele inventa id, sem as regras
    ele monta o que será recusado, e sem a posição não tem o que analisar. Uma
    chamada por assunto viraria três idas ao servidor antes de qualquer resposta.
    """

    http_method_names = ['get']

    # Janela analítica. Doze meses cobrem a comparação com o mesmo mês do ano
    # passado, que é a pergunta mais comum, sem trazer histórico inteiro.
    DEFAULT_MONTHS = 12
    MAX_MONTHS = 120

    DEFAULT_TRANSACTIONS = 25
    MAX_TRANSACTIONS = 200

    def read_int(self, name, default, maximum):
        """Parâmetro numérico da querystring, preso entre 1 e o teto."""
        raw = self.request.GET.get(name)
        if not raw or not raw.lstrip('-').isdigit():
            return default
        return max(1, min(int(raw), maximum))

    def get(self, request, *args, **kwargs):
        user = request.api_user
        today = timezone.localdate()

        months = self.read_int('months', self.DEFAULT_MONTHS, self.MAX_MONTHS)
        limit = self.read_int('transactions', self.DEFAULT_TRANSACTIONS, self.MAX_TRANSACTIONS)

        # O primeiro dia do mês que abre a janela: recortar por dia deixaria o
        # mês mais antigo pela metade e a série começaria com um degrau falso.
        start = (today.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)

        owned = Transaction.objects.filter(user=user)
        settled = owned.filter(method__in=SETTLED_METHODS)

        return json_response({
            'ok': True,
            'generated_at': timezone.localtime(),
            'user': {'id': user.id, 'username': user.username},
            'window': {'start': start, 'end': today, 'months': months},
            'documentation': DOCUMENTATION,
            'options': self.options(user, today),
            'position': self.position(user, settled),
            'analysis': self.analysis(settled, start, today),
            'forecast': self.forecast(owned, today),
            'recent_transactions': [
                serialize_transaction(t)
                for t in owned.select_related('account', 'card__account', 'category').order_by('-datetime')[:limit]
            ],
        })

    # -- opções -----------------------------------------------------------

    def options(self, user, today):
        """Tudo que pode ser escolhido num lançamento, com o que cada conta aceita.

        Conta e categoria são cadastros globais, sem dono: todo usuário enxerga
        os mesmos. Cartão tem dono, e por isso é filtrado.
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

    # -- posição ----------------------------------------------------------

    def position(self, user, settled):
        """Onde o dinheiro está agora: saldo por conta e posição investida.

        Posição acumulada ignora recorte de período e conta todas as naturezas —
        é para isso que interna e ajuste existem.
        """
        by_account = {}
        for row in settled.values('account_id', 'account__description', 'type').annotate(total=Sum('value')):
            entry = by_account.setdefault(
                row['account_id'],
                {'id': row['account_id'], 'description': row['account__description'], 'income': ZERO, 'outcome': ZERO},
            )
            entry['income' if row['type'] == Type.IN else 'outcome'] += row['total']

        accounts = []
        for entry in by_account.values():
            accounts.append({**entry, 'balance': money(entry['income'] - entry['outcome'])})
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
            'balance': money(balance),
            'invested': money(invested_total),
            'total': money(balance + invested_total),
            'accounts': accounts,
            'investments': serialized_investments,
        }

    # -- análise ----------------------------------------------------------

    def analysis(self, settled, start, end):
        """Séries do período: por mês e por categoria, só natureza REGULAR.

        Interna e ajuste ficam de fora porque remanejam ou corrigem saldo, e
        inflariam entrada e saída dos dois lados — mesmo recorte dos painéis.
        """
        window = settled.filter(nature=Nature.REGULAR, datetime__date__gte=start, datetime__date__lte=end)

        by_month = {}
        for row in window.annotate(month=TruncMonth('datetime')).values('month', 'type').annotate(total=Sum('value')):
            key = row['month'].strftime('%Y-%m')
            entry = by_month.setdefault(key, {'month': key, 'income': ZERO, 'outcome': ZERO})
            entry['income' if row['type'] == Type.IN else 'outcome'] += row['total']

        monthly = []
        for entry in sorted(by_month.values(), key=lambda item: item['month']):
            monthly.append({
                'month': entry['month'],
                'income': money(entry['income']),
                'outcome': money(entry['outcome']),
                'net': money(entry['income'] - entry['outcome']),
            })

        totals = {row['type']: row['total'] for row in window.values('type').annotate(total=Sum('value'))}
        income = money(totals.get(Type.IN))
        outcome = money(totals.get(Type.OUT))

        by_category = [
            {'category': row['category__description'] or 'Categoria Não Identificada', 'outcome': money(row['total'])}
            for row in window.filter(type=Type.OUT)
            .values('category__description')
            .annotate(total=Sum('value'))
            .order_by('-total')
        ]

        return {
            'income': income,
            'outcome': outcome,
            'net': money(income - outcome),
            'monthly': monthly,
            'outcome_by_category': by_category,
        }

    # -- previsão ---------------------------------------------------------

    def forecast(self, owned, today):
        """Gasto no crédito já assumido que ainda vai vencer, nos 12 meses à frente."""
        window = owned.filter(
            method=Method.CREDIT,
            type=Type.OUT,
            nature=Nature.REGULAR,
            datetime__date__gte=today,
            datetime__date__lte=today + timedelta(days=365),
        )

        monthly = [
            {'month': row['month'].strftime('%Y-%m'), 'outcome': money(row['total'])}
            for row in window.annotate(month=TruncMonth('datetime'))
            .values('month')
            .annotate(total=Sum('value'))
            .order_by('month')
        ]

        by_category = [
            {'category': row['category__description'] or 'Categoria Não Identificada', 'outcome': money(row['total'])}
            for row in window.values('category__description').annotate(total=Sum('value')).order_by('-total')
        ]

        return {
            'total': money(window.aggregate(total=Sum('value'))['total']),
            'monthly': monthly,
            'outcome_by_category': by_category,
        }


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
