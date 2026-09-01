from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN
import hashlib
import secrets
from django.conf import settings
from django.db import models
from django.contrib.auth.models import AbstractUser, Group as BaseGroup
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.utils import timezone


# Sábado e domingo no weekday() do Python, que conta a partir da segunda.
WEEKEND = (5, 6)


def add_months(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])

    return dt.replace(year=year, month=month, day=day)


def next_business_day(day):
    """Empurra sábado e domingo para a segunda-feira seguinte.

    Feriado não entra na conta: o sistema não mantém calendário deles, e supor
    um significaria escolher entre nacional, estadual e municipal sem ter como
    saber qual vale para o cartão.
    """
    while day.weekday() in WEEKEND:
        day += timedelta(days=1)
    return day


class User(AbstractUser):
    email = models.EmailField(blank=True, null=True, verbose_name='Endereço de email')
    observations = models.TextField(blank=True, null=True, verbose_name='Observações')

    def clean(self):
        super().clean()
        if self.email:
            email = User.objects.filter(email=self.email).exclude(pk=self.pk)
            if email.exists():
                raise ValidationError({'email': 'Já existe um usuário com este e-mail.'})


class Group(BaseGroup):
    class Meta:
        proxy = True
        verbose_name = BaseGroup._meta.verbose_name
        verbose_name_plural = BaseGroup._meta.verbose_name_plural
        app_label = 'app'


class ApiToken(models.Model):
    """Credencial de acesso à API, usada por agentes externos em nome de um usuário.

    O token em claro existe uma única vez, no instante em que é gerado: o que fica
    gravado é o SHA-256 dele. Não é Argon2, como a senha, porque aqui a busca é por
    igualdade — a requisição chega com o token e precisa encontrar a linha dele numa
    consulta só. Argon2 sorteia um sal por registro, e com isso não há o que procurar
    no índice: seria preciso varrer a tabela inteira comparando um a um.

    E o que o sal protege não existe neste caso. Ele encarece a força bruta sobre
    senha curta, escolhida por gente e repetida entre sites; o token são 32 bytes
    sorteados, fora do alcance de dicionário. Sem sal, o resumo é determinístico e
    o índice único resolve a busca.
    """

    # Prefixo no token em claro para ele ser reconhecível quando aparecer num log,
    # numa captura de tela ou num nó do n8n: quem encontra a chave sabe de onde ela é.
    PREFIX = 'finflow_'

    # 32 bytes sorteados: o suficiente para a adivinhação ser inviável, e o que
    # secrets.token_urlsafe transforma em texto seguro para cabeçalho HTTP.
    ENTROPY_BYTES = 32

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='api_tokens', verbose_name='Usuário')
    description = models.CharField(max_length=100, verbose_name='Descrição', help_text='Onde esta credencial é usada. Ex.: Agente n8n.')
    digest = models.CharField(max_length=64, unique=True, editable=False, verbose_name='Resumo do Token')
    is_active = models.BooleanField(default=True, verbose_name='Ativa')
    created = models.DateTimeField(auto_now_add=True, verbose_name='Criada em')
    last_used = models.DateTimeField(blank=True, null=True, editable=False, verbose_name='Último Uso')

    @staticmethod
    def digest_for(raw):
        """Resumo do token: o que se grava e o que se procura."""
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, user, description):
        """Cria a credencial e devolve o par (registro, token em claro).

        O token em claro é devolvido, e não gravado: esta é a única vez em que ele
        existe fora de quem o pediu. Perdido, não se recupera — emite-se outro.
        """
        raw = f'{cls.PREFIX}{secrets.token_urlsafe(cls.ENTROPY_BYTES)}'
        return cls.objects.create(user=user, description=description, digest=cls.digest_for(raw)), raw

    @classmethod
    def resolve(cls, raw):
        """Credencial ativa correspondente ao token, ou None.

        A comparação é feita pelo índice único sobre o resumo, não campo a campo em
        Python: não há laço cujo tempo varie com o quanto do token o atacante já
        acertou, que é o que a comparação em tempo constante evitaria.
        """
        if not raw:
            return None
        return cls.objects.select_related('user').filter(digest=cls.digest_for(raw), is_active=True).first()

    def touch(self):
        """Marca o uso, sem mexer no resto do registro nem disparar validação."""
        self.last_used = timezone.now()
        ApiToken.objects.filter(pk=self.pk).update(last_used=self.last_used)

    def __str__(self):
        return f'{self.description} ({self.user})'

    class Meta:
        ordering = ['user__username', 'description']
        unique_together = ('user', 'description')
        verbose_name = 'Credencial de API'
        verbose_name_plural = 'Credenciais de API'


class Type(models.TextChoices):
    IN = 'IN', 'Entrada'
    OUT = 'OUT', 'Saída'


class Method(models.TextChoices):
    CREDIT = 'CREDIT', 'Crédito'
    DEBIT = 'DEBIT', 'Débito'
    NOT_APPLICABLE = 'NOT_APPLICABLE', 'Não Se Aplica'


class Nature(models.TextChoices):
    REGULAR = 'REGULAR', 'Normal'
    INTERNAL = 'INTERNAL', 'Movimentação Interna'
    ADJUSTMENT = 'ADJUSTMENT', 'Ajuste de Saldo'


class Account(models.Model):
    description = models.CharField(max_length=100, unique=True, verbose_name='Conta')

    def __str__(self):
        return self.description

    class Meta:
        ordering = ['description']
        verbose_name = 'Conta'
        verbose_name_plural = 'Contas'


class Category(models.Model):
    description = models.CharField(max_length=100, unique=True, verbose_name='Categoria')

    def __str__(self):
        return self.description

    class Meta:
        ordering = ['description']
        verbose_name = 'Categoria'
        verbose_name_plural = 'Categorias'


class BusinessRule(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, verbose_name='Conta')
    type = models.CharField(max_length=20, choices=Type.choices, verbose_name='Tipo')
    method = models.CharField(max_length=20, choices=Method.choices, verbose_name='Método')

    def __str__(self):
        return f'{self.account} / {self.get_type_display()} / {self.get_method_display()}'

    class Meta:
        ordering = ['account__description', 'type', 'method']
        unique_together = ('account', 'type', 'method')
        verbose_name = 'Regra de Negócio'
        verbose_name_plural = 'Regras de Negócio'


class Card(models.Model):
    """Cartão de crédito de uma conta, e o ciclo que decide a data das compras.

    Fechamento e vencimento são guardados como dia do mês, não como data: o
    ciclo se repete todo mês, e o que a compra precisa saber é em qual volta
    dele ela caiu.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cards', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='cards', verbose_name='Conta')
    last_digits = models.CharField(max_length=4, validators=[RegexValidator(r'^\d{4}$', 'Informe exatamente os quatro últimos dígitos.')], verbose_name='Últimos Quatro Dígitos')
    closing_day = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(31)], verbose_name='Dia de Fechamento')
    due_day = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(31)], verbose_name='Dia de Vencimento')

    TYPE = Type.OUT
    METHOD = Method.CREDIT

    def clean(self):
        super().clean()
        # Mesma checagem do Parcelamento: um cartão numa conta que não aceita
        # saída em crédito não teria como lançar uma única compra.
        if self.account_id and not BusinessRule.objects.filter(account=self.account, type=self.TYPE, method=self.METHOD).exists():
            raise ValidationError({'account': f'A conta não permite {Type(self.TYPE).label.lower()} em {Method(self.METHOD).label}, necessário para registrar as compras do cartão.'})

    def cycle_day(self, year, month, day):
        """Um dia do ciclo num mês, limitado ao tamanho dele.

        Quem fecha dia 31 fecha dia 28 em fevereiro: o dia configurado nunca
        sai do mês a que pertence.
        """
        return date(year, month, min(day, monthrange(year, month)[1]))

    def closing_date(self, year, month):
        """Dia em que fecha a fatura do mês informado.

        É sempre o dia informado no cadastro, mesmo em sábado ou domingo:
        fechar é a operadora encerrar a fatura, e para isso não é preciso
        banco aberto.
        """
        return self.cycle_day(year, month, self.closing_day)

    def due_date(self, year, month):
        """Vencimento, em dia útil, da fatura que fecha no mês informado.

        Vencer não é fechar: quando o dia de vencimento não passa o de
        fechamento, ele é do mês seguinte. Um cartão que fecha dia 25 e vence
        dia 5 vence sempre no mês depois daquele em que fechou.

        E, ao contrário do fechamento, pagar depende de banco aberto: um
        vencimento que cai no fim de semana anda para a segunda-feira.
        """
        if self.due_day <= self.closing_day:
            reference = add_months(date(year, month, 1), 1)
            year, month = reference.year, reference.month
        return next_business_day(self.cycle_day(year, month, self.due_day))

    def invoice_cycle(self, day):
        """Mês da fatura que recebe uma compra feita em `day`.

        É a primeira que ainda não fechou: comprou no dia do fechamento ou
        depois, cai na seguinte; antes disso, na atual. Basta olhar o mês da
        compra, porque o fechamento nunca escorrega para fora do mês dele.
        """
        cycle = date(day.year, day.month, 1)
        while self.closing_date(cycle.year, cycle.month) <= day:
            cycle = add_months(cycle, 1)
        return cycle

    def invoice_due_date(self, day, cycles=0):
        """Vencimento que uma compra feita em `day` vai carregar.

        `cycles` adianta faturas, para as parcelas: 0 é a que recebeu a compra,
        1 é a de um mês depois. Cada uma tem o vencimento calculado do próprio
        ciclo, e não somando um mês sobre a anterior — senão a parcela seguinte
        herdaria o empurrão de fim de semana que só valia para a primeira.
        """
        cycle = add_months(self.invoice_cycle(day), cycles)
        return self.due_date(cycle.year, cycle.month)

    def invoice_datetime(self, moment, cycles=0):
        """Data e hora com que a compra feita em `moment` entra na conta.

        A hora informada é preservada: quem decide a fatura é o dia. A conversão
        para o fuso local vem antes da comparação porque é o calendário do
        usuário, não o UTC, que diz se a compra passou do fechamento.
        """
        local = timezone.localtime(moment) if timezone.is_aware(moment) else moment
        due = self.invoice_due_date(local.date(), cycles)
        return local.replace(year=due.year, month=due.month, day=due.day)

    @property
    def in_use(self):
        """O cartão já tem lançamento preso a ele, e por isso não se apaga."""
        return self.transactions.exists() or self.installments.exists()

    def __str__(self):
        return f'{self.account} (final {self.last_digits})'

    class Meta:
        ordering = ['account__description', 'last_digits']
        # O final se repete entre usuários: dois cartões distintos podem
        # terminar nos mesmos quatro dígitos, e a conta é cadastro global.
        unique_together = ('user', 'account', 'last_digits')
        verbose_name = 'Cartão'
        verbose_name_plural = 'Cartões'


class Installment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='installments', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, verbose_name='Conta')
    card = models.ForeignKey(Card, on_delete=models.PROTECT, blank=True, null=True, related_name='installments', verbose_name='Cartão')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, verbose_name='Categoria')
    description = models.CharField(max_length=200, blank=True, null=True, verbose_name='Descrição')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor Total')
    installments = models.PositiveSmallIntegerField(verbose_name='Número de Parcelas')
    datetime = models.DateTimeField(verbose_name='Data e Hora da Compra')

    TYPE = Type.OUT
    METHOD = Method.CREDIT

    def clean(self):
        super().clean()
        if self.account_id:
            if not BusinessRule.objects.filter(account=self.account, type=self.TYPE, method=self.METHOD).exists():
                raise ValidationError(f'A conta não permite {Type(self.TYPE).label.lower()} em {Method(self.METHOD).label}, necessário para registrar as parcelas.')
        if self.installments is not None and self.installments < 2:
            raise ValidationError({'installments': 'Um parcelamento deve ter no mínimo 2 parcelas.'})
        if self.card_id:
            if self.account_id and self.card.account_id != self.account_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outra conta.'})
            if self.user_id and self.card.user_id != self.user_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outro usuário.'})

    def generate_transactions(self):
        """Cria uma transação por parcela, a partir da data da compra.

        Com cartão, a 1ª parcela cai no vencimento da fatura em que a compra
        entrou, e as demais somam um mês a partir dele. Sem cartão não há ciclo
        a consultar, e a data informada é a da própria 1ª parcela.

        O cálculo parte sempre de `datetime`, que guarda a compra e não o
        vencimento: regerar as parcelas dá o mesmo resultado quantas vezes for.
        """
        self.transactions.all().delete()

        base_value = (self.value / self.installments).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        last_value = self.value - base_value * (self.installments - 1)

        transactions = []
        for i in range(self.installments):
            parcel_value = last_value if i == self.installments - 1 else base_value
            parcel_datetime = self.card.invoice_datetime(self.datetime, i) if self.card_id else add_months(self.datetime, i)
            transactions.append(Transaction(
                user=self.user,
                account=self.account,
                card=self.card,
                type=self.TYPE,
                method=self.METHOD,
                category=self.category,
                description=self.description,
                value=parcel_value,
                datetime=parcel_datetime,
                installment=self,
                parcel=i + 1,
            ))

        Transaction.objects.bulk_create(transactions)

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new:
            self.generate_transactions()

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    def __str__(self):
        return f'R${self.value} ({self.installments}x)'

    class Meta:
        ordering = ['-datetime']
        verbose_name = 'Parcelamento'
        verbose_name_plural = 'Parcelamentos'


class Investment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='investments', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, verbose_name='Conta')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, verbose_name='Categoria')
    description = models.CharField(max_length=200, verbose_name='Investimento', help_text='Como está investido. Ex.: CDI, Tesouro Selic, LCI.')

    METHOD = Method.DEBIT
    REDEMPTION_METHOD = Method.NOT_APPLICABLE

    def clean(self):
        super().clean()
        if self.account_id:
            if not BusinessRule.objects.filter(account=self.account, type=Type.OUT, method=self.METHOD).exists():
                raise ValidationError(f'A conta não permite saída em {Method(self.METHOD).label}, necessário para registrar as aplicações.')
            if not BusinessRule.objects.filter(account=self.account, type=Type.IN, method=self.REDEMPTION_METHOD).exists():
                raise ValidationError(f'A conta não permite entrada em {Method(self.REDEMPTION_METHOD).label}, necessário para registrar os resgates.')

    @property
    def applied_value(self):
        return self.contributions.aggregate(total=models.Sum('value'))['total'] or Decimal('0.00')

    @property
    def redeemed_value(self):
        return self.redemptions.aggregate(total=models.Sum('value'))['total'] or Decimal('0.00')

    @property
    def yielded_value(self):
        return self.yields.aggregate(total=models.Sum('value'))['total'] or Decimal('0.00')

    @property
    def balance(self):
        return self.applied_value + self.yielded_value - self.redeemed_value

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    def __str__(self):
        return self.description

    class Meta:
        ordering = ['description']
        unique_together = ('user', 'account', 'description')
        verbose_name = 'Investimento'
        verbose_name_plural = 'Investimentos'


class InvestmentEntry(models.Model):
    TYPE = None
    VALUE_LABEL = 'Valor'
    GENERATES_TRANSACTION = True

    investment = models.ForeignKey(Investment, on_delete=models.CASCADE, related_name='%(class)ss', verbose_name='Investimento')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor')
    datetime = models.DateTimeField(verbose_name='Data e Hora')

    def clean(self):
        super().clean()
        if self.value is not None and self.value <= 0:
            raise ValidationError({'value': f'O {self.VALUE_LABEL.lower()} deve ser maior que zero.'})

    def generate_transactions(self):
        self.transactions.all().delete()

        investment = self.investment
        Transaction.objects.create(
            user=investment.user,
            account=investment.account,
            type=self.TYPE,
            method=investment.METHOD if self.TYPE == Type.OUT else investment.REDEMPTION_METHOD,
            category=investment.category,
            description=investment.description,
            value=self.value,
            datetime=self.datetime,
            investment=investment,
            **{self._meta.model_name: self},
        )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.GENERATES_TRANSACTION:
            self.generate_transactions()

    def __str__(self):
        return f'R${self.value} ({self.datetime:%d/%m/%Y})'

    class Meta:
        abstract = True
        ordering = ['-datetime']


class Contribution(InvestmentEntry):
    TYPE = Type.OUT
    VALUE_LABEL = 'Valor Aplicado'

    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor Aplicado')

    class Meta(InvestmentEntry.Meta):
        abstract = False
        verbose_name = 'Aplicação'
        verbose_name_plural = 'Aplicações'


class Redemption(InvestmentEntry):
    TYPE = Type.IN
    VALUE_LABEL = 'Valor Resgatado'

    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor Resgatado')

    class Meta(InvestmentEntry.Meta):
        abstract = False
        verbose_name = 'Resgate'
        verbose_name_plural = 'Resgates'


class Yield(InvestmentEntry):
    VALUE_LABEL = 'Valor Rendido'
    GENERATES_TRANSACTION = False

    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor Rendido')

    class Meta(InvestmentEntry.Meta):
        abstract = False
        verbose_name = 'Rendimento'
        verbose_name_plural = 'Rendimentos'


class Transfer(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='transfers', verbose_name='Usuário')
    origin = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='transfers_out', verbose_name='Conta de Origem')
    destination = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='transfers_in', verbose_name='Conta de Destino')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, verbose_name='Categoria')
    description = models.CharField(max_length=200, blank=True, null=True, verbose_name='Descrição')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor')
    datetime = models.DateTimeField(verbose_name='Data e Hora')

    # A saída é débito, dinheiro que já deixou a conta. A entrada é não se
    # aplica: o dinheiro só voltou para o próprio usuário, e classificá-la como
    # débito a colocaria no mesmo balde de uma receita de verdade.
    METHOD = Method.DEBIT
    DESTINATION_METHOD = Method.NOT_APPLICABLE

    def clean(self):
        super().clean()

        if self.origin_id and self.destination_id and self.origin_id == self.destination_id:
            raise ValidationError({'destination': 'A conta de destino deve ser diferente da conta de origem.'})

        if self.value is not None and self.value <= 0:
            raise ValidationError({'value': 'O valor deve ser maior que zero.'})

        # Mesma checagem do Parcelamento, uma perna de cada vez: sem as duas
        # regras a transferência gravaria metade e deixaria o saldo torto.
        if self.origin_id and not BusinessRule.objects.filter(account=self.origin, type=Type.OUT, method=self.METHOD).exists():
            raise ValidationError({'origin': f'A conta de origem não permite saída em {Method(self.METHOD).label}, necessário para registrar a transferência.'})

        if self.destination_id and not BusinessRule.objects.filter(account=self.destination, type=Type.IN, method=self.DESTINATION_METHOD).exists():
            raise ValidationError({'destination': f'A conta de destino não permite entrada em {Method(self.DESTINATION_METHOD).label}, necessário para registrar a transferência.'})

    def generate_transactions(self):
        self.transactions.all().delete()

        common = {
            'user': self.user,
            'nature': Nature.INTERNAL,
            'category': self.category,
            'description': self.description,
            'value': self.value,
            'datetime': self.datetime,
            'transfer': self,
        }

        Transaction.objects.bulk_create([
            Transaction(account=self.origin, type=Type.OUT, method=self.METHOD, **common),
            Transaction(account=self.destination, type=Type.IN, method=self.DESTINATION_METHOD, **common),
        ])

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new:
            self.generate_transactions()

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    def __str__(self):
        return f'R${self.value} ({self.origin} → {self.destination})'

    class Meta:
        ordering = ['-datetime']
        verbose_name = 'Transferência'
        verbose_name_plural = 'Transferências'


class Transaction(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='transactions_owned', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, verbose_name='Conta')
    card = models.ForeignKey(Card, on_delete=models.PROTECT, blank=True, null=True, related_name='transactions', verbose_name='Cartão')
    type = models.CharField(max_length=20, choices=Type.choices, verbose_name='Tipo')
    method = models.CharField(max_length=20, choices=Method.choices, verbose_name='Método')
    nature = models.CharField(max_length=20, choices=Nature.choices, default=Nature.REGULAR, verbose_name='Natureza')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, verbose_name='Categoria')
    description = models.CharField(max_length=200, blank=True, null=True, verbose_name='Descrição')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor')
    datetime = models.DateTimeField(verbose_name='Data e Hora')

    installment = models.ForeignKey(Installment, on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Parcelamento')
    parcel = models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Parcela')

    investment = models.ForeignKey(Investment, on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Investimento')
    contribution = models.ForeignKey(Contribution, on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Aplicação')
    redemption = models.ForeignKey(Redemption, on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Resgate')

    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Transferência')

    def clean(self):
        super().clean()
        if self.account_id and self.type and self.method:
            if not BusinessRule.objects.filter(account=self.account, type=self.type, method=self.method).exists():
                raise ValidationError('Combinação de conta, tipo e método não permitida pelas regras de negócio.')
        if self.card_id:
            # O cartão é o que dita a data da compra, e só faz sentido no dono
            # dele, na conta dele e no método que ele representa.
            if self.account_id and self.card.account_id != self.account_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outra conta.'})
            if self.user_id and self.card.user_id != self.user_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outro usuário.'})
            if self.method and self.method != Method.CREDIT:
                raise ValidationError({'card': f'O cartão só se aplica a lançamentos em {Method.CREDIT.label}.'})

    # Espelho do is_derived para uso em queryset, onde a property não alcança.
    # Ficam juntos de propósito: uma origem nova tem de entrar nos dois.
    DERIVED_FIELDS = ('installment', 'investment', 'transfer')

    # Origens que o usuário pode apagar pela tela de transações, levando junto
    # as transações que elas geraram. Investimento fica de fora: ele acumula
    # aplicações, resgates e rendimentos, e apagar tudo isso a partir de uma
    # única transação seria destrutivo demais para o gesto que o usuário fez.
    #
    # As frases moram aqui, e não no template ou no JS, porque dependem do
    # gênero de cada origem: 'warning' avisa antes, na confirmação, e 'success'
    # confirma o que saiu depois.
    DELETABLE_ORIGINS = {
        'installment': {
            'warning': 'Esta é uma parcela: o parcelamento será removido por inteiro, com todas as suas parcelas.',
            'success': 'Parcelamento removido com sucesso, junto de todas as suas parcelas.',
        },
        'transfer': {
            'warning': 'Esta é uma perna de transferência: a transferência será removida por inteiro, com as duas transações que ela gerou.',
            'success': 'Transferência removida com sucesso, junto das duas transações que ela gerou.',
        },
    }

    @property
    def is_derived(self):
        return bool(self.installment_id or self.investment_id or self.transfer_id)

    @property
    def deletable_origin_field(self):
        """Nome do campo de origem que esta transação apaga junto de si.

        Devolve None para a avulsa (que se apaga sozinha) e para a de
        investimento (cuja origem não é removível por aqui). Responde sem
        carregar a origem, para a listagem poder perguntar linha a linha.
        """
        for field in self.DELETABLE_ORIGINS:
            if getattr(self, f'{field}_id'):
                return field
        return None

    @property
    def is_deletable(self):
        """A linha oferece o botão de remover: avulsa ou de origem removível."""
        return not self.is_derived or self.deletable_origin_field is not None

    @property
    def delete_warning(self):
        """Aviso do que mais sai junto, exibido na confirmação. Vazio quando a
        transação se apaga sozinha."""
        field = self.deletable_origin_field
        return self.DELETABLE_ORIGINS[field]['warning'] if field else ''

    @property
    def deletable_origin(self):
        """Registro de origem que esta transação apaga junto de si, e a frase
        que confirma o que saiu. (None, None) quando ela se apaga sozinha."""
        field = self.deletable_origin_field
        if not field:
            return None, None
        return getattr(self, field), self.DELETABLE_ORIGINS[field]['success']

    @classmethod
    def derived_q(cls):
        """Filtro das transações que têm registro de origem."""
        query = models.Q()
        for field in cls.DERIVED_FIELDS:
            query |= models.Q(**{f'{field}__isnull': False})
        return query

    @classmethod
    def standalone_filters(cls):
        """Filtro inverso: só as transações avulsas, que se editam direto."""
        return {f'{field}__isnull': True for field in cls.DERIVED_FIELDS}

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    @property
    def origin_display(self):
        for field in self.DERIVED_FIELDS:
            if getattr(self, f'{field}_id'):
                return self._meta.get_field(field).verbose_name
        return ''

    def __str__(self):
        if self.installment_id:
            return f'{self.category_display} (R${self.value}) - {self.parcel}/{self.installment.installments}'
        if self.redemption_id:
            return f'{self.category_display} (R${self.value}) - Resgate'
        if self.contribution_id:
            return f'{self.category_display} (R${self.value}) - Aplicação'
        if self.transfer_id:
            sentido = 'Envio' if self.type == Type.OUT else 'Recebimento'
            return f'{self.category_display} (R${self.value}) - Transferência ({sentido})'
        return f'{self.category_display} (R${self.value})'

    class Meta:
        ordering = ['-datetime']
        verbose_name = 'Transação'
        verbose_name_plural = 'Transações'
