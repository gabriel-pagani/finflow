from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models, transaction as db_transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
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


def current_reference():
    """A competência de hoje: o primeiro dia do mês corrente.

    Default da primeira competência de uma assinatura. É função, e não o valor
    calculado na importação, senão todo cadastro feito depois do deploy nasceria
    com o mês em que o processo subiu.
    """
    return timezone.localdate().replace(day=1)


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


class Recurrence(models.TextChoices):
    """De quanto em quanto tempo uma assinatura cobra.

    Todas são múltiplos de mês, e é isso que deixa a competência continuar sendo
    o mês: entre uma anual e uma mensal muda o tamanho do passo, não a unidade.
    """

    MONTHLY = 'MONTHLY', 'Mensal'
    BIMONTHLY = 'BIMONTHLY', 'Bimestral'
    QUARTERLY = 'QUARTERLY', 'Trimestral'
    SEMIANNUAL = 'SEMIANNUAL', 'Semestral'
    ANNUAL = 'ANNUAL', 'Anual'


# O passo de cada recorrência, em meses. Fica fora do TextChoices porque o que
# se escreve no corpo dele vira opção do select: um dicionário ali apareceria
# para o usuário como uma recorrência chamada "Months".
RECURRENCE_MONTHS = {
    Recurrence.MONTHLY: 1,
    Recurrence.BIMONTHLY: 2,
    Recurrence.QUARTERLY: 3,
    Recurrence.SEMIANNUAL: 6,
    Recurrence.ANNUAL: 12,
}


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
        """O cartão já tem registro preso a ele, e por isso não se apaga.

        Assinatura entra na conta como transação e parcelamento: ela aponta
        para o cartão com PROTECT, e sem esta checagem a remoção morreria como
        erro de banco em vez de virar recado na tela.
        """
        return self.transactions.exists() or self.installments.exists() or self.subscriptions.exists()

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


class Subscription(models.Model):
    """Assinatura recorrente: um valor que volta todo mês no mesmo cartão.

    O cadastro é o molde da cobrança, não a cobrança. Ele guarda o que cada mês
    vai lançar — conta, cartão, categoria, descrição e valor — e a cada
    competência vencida sai uma transação nova montada a partir dele.

    A transação, depois de criada, se solta do molde. Ela é comum: editável,
    removível, e sobrevive à assinatura, porque o vínculo é SET_NULL. Cancelar
    o Spotify não apaga o que já foi pago a ele — o que houve de dinheiro
    continua no extrato, que é justamente o que um sistema de finanças precisa
    lembrar depois que a assinatura acaba.

    Também por isso a geração não mora no save(), como a do Parcelamento. Um
    parcelamento tem fim e sai inteiro numa vez só; uma assinatura não tem fim, e
    o que ela deve ter gerado depende de que dia é hoje. Quem gera é o
    `generate_due`, chamado pelo comando do cron e pelas telas.

    Alterar o cadastro vale para as próximas cobranças. As que já saíram
    guardam o valor e a data que valiam quando saíram: recalculá-las mudaria
    fatura que o usuário já conferiu, e apagaria o histórico de um preço que
    subiu.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscriptions', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, verbose_name='Conta')
    card = models.ForeignKey(Card, on_delete=models.PROTECT, related_name='subscriptions', verbose_name='Cartão')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, verbose_name='Categoria')
    description = models.CharField(max_length=200, verbose_name='Assinatura', help_text='O nome do serviço. Ex.: Spotify, Netflix, Claude.')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor da Cobrança')
    charge_day = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(31)], verbose_name='Dia da Cobrança')
    recurrence = models.CharField(max_length=20, choices=Recurrence.choices, default=Recurrence.MONTHLY, verbose_name='Recorrência')

    # A competência é o mês, e por isso as duas datas ficam no dia 1º. `start` é
    # o primeiro mês que a assinatura cobra — o do cadastro, porque assinatura
    # nova não retroage — e `last_reference` é o último já lançado, que é o que
    # impede a mesma cobrança de sair duas vezes.
    start = models.DateField(default=current_reference, verbose_name='Primeira Competência')
    last_reference = models.DateField(blank=True, null=True, verbose_name='Última Competência Gerada')

    TYPE = Type.OUT
    METHOD = Method.CREDIT

    def clean(self):
        super().clean()

        if self.account_id and not BusinessRule.objects.filter(account=self.account, type=self.TYPE, method=self.METHOD).exists():
            raise ValidationError({'account': f'A conta não permite {Type(self.TYPE).label.lower()} em {Method(self.METHOD).label}, necessário para registrar as cobranças da assinatura.'})

        if self.card_id:
            if self.account_id and self.card.account_id != self.account_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outra conta.'})
            if self.user_id and self.card.user_id != self.user_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outro usuário.'})

        if self.value is not None and self.value <= 0:
            raise ValidationError({'value': 'O valor deve ser maior que zero.'})

    @property
    def interval(self):
        """Quantos meses separam uma cobrança da seguinte."""
        return RECURRENCE_MONTHS[self.recurrence]

    @property
    def next_reference(self):
        """Competência da próxima cobrança: a que sai assim que o dia dela chegar.

        Sai do que já foi gerado, e não do calendário: a anual cadastrada em
        março cobra em março do ano seguinte, não em janeiro. Antes da primeira
        cobrança, é o próprio `start`.
        """
        return add_months(self.last_reference, self.interval) if self.last_reference else self.start

    def charge_date(self, reference):
        """Dia em que a assinatura cobra, dentro do mês da competência.

        Quem cobra dia 31 cobra dia 28 em fevereiro: o dia escolhido nunca sai
        do mês a que pertence, pela mesma conta que o ciclo do cartão faz.
        """
        return self.card.cycle_day(reference.year, reference.month, self.charge_day)

    def charge_datetime(self, reference):
        """A cobrança como data e hora, que é o que o ciclo do cartão consome.

        Meio-dia, e não meia-noite: a hora aqui é arbitrária — quem decide a
        fatura é o dia —, e meia-noite é justamente o horário que deixa de
        existir em algumas mudanças de fuso.
        """
        return timezone.make_aware(datetime.combine(self.charge_date(reference), time(12, 0)))

    def create_transaction(self, reference):
        """A cobrança de uma competência, na fatura em que ela cai.

        A data gravada é a do vencimento da fatura, como em toda compra no
        crédito: o dinheiro sai quando a fatura vence, não no dia em que o
        serviço cobrou.
        """
        return Transaction.objects.create(
            user=self.user,
            account=self.account,
            card=self.card,
            type=self.TYPE,
            method=self.METHOD,
            category=self.category,
            description=self.description,
            value=self.value,
            datetime=self.card.invoice_datetime(self.charge_datetime(reference)),
            subscription=self,
            reference=reference,
        )

    def generate_charges(self, today=None):
        """Lança o que já venceu e ainda não saiu, uma transação por competência.

        Vencido é o mês cujo dia de cobrança já chegou: quem cobra dia 10 só
        lança no dia 10, e não no 1º. Meses anteriores a `start` nunca entram —
        assinatura cadastrada hoje não inventa histórico.
        """
        today = today or timezone.localdate()
        created = []

        # A saída barata, antes de abrir transação e travar a linha: este
        # caminho roda a cada página aberta, e uma assinatura anual passa onze
        # meses do ano sem nada a fazer.
        if self.charge_date(self.next_reference) > today:
            return created

        with db_transaction.atomic():
            # O cron e a tela podem chegar ao mesmo tempo, e o que decide a
            # próxima competência é o que já foi gerado. Sem o lock, os dois
            # leriam a mesma linha e lançariam a mesma cobrança duas vezes.
            locked = Subscription.objects.select_for_update().get(pk=self.pk)

            reference = locked.next_reference
            current = today.replace(day=1)

            while reference <= current:
                if self.charge_date(reference) > today:
                    break
                created.append(self.create_transaction(reference))
                locked.last_reference = reference
                reference = add_months(reference, self.interval)

            if created:
                locked.save(update_fields=['last_reference'])
                self.last_reference = locked.last_reference

        return created

    @classmethod
    def generate_due(cls, user=None, today=None):
        """Gera as cobranças vencidas e devolve quantas saíram.

        É o único caminho de geração: o comando do cron chama sem usuário, para
        todas; as telas chamam com o usuário logado, para que quem abre o
        sistema não precise esperar o cron do dia seguinte para ver a cobrança
        do mês.

        O filtro é o que deixa a chamada barata na tela: quem já tem a
        competência deste mês lançada nem chega a ser carregado.
        """
        today = today or timezone.localdate()

        pending = cls.objects.filter(
            models.Q(last_reference__isnull=True) | models.Q(last_reference__lt=today.replace(day=1))
        ).select_related('card', 'account', 'category')

        if user is not None:
            pending = pending.filter(user=user)

        return sum(len(subscription.generate_charges(today)) for subscription in pending)

    def save(self, *args, **kwargs):
        # Competência é mês: o dia informado não importa, e guardá-lo faria a
        # comparação com `last_reference` depender de dois dias diferentes.
        if self.start:
            self.start = self.start.replace(day=1)
        super().save(*args, **kwargs)

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    def __str__(self):
        return f'{self.description} (R${self.value} · {self.get_recurrence_display()})'

    class Meta:
        ordering = ['description']
        verbose_name = 'Assinatura'
        verbose_name_plural = 'Assinaturas'


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

    # SET_NULL, e não CASCADE como as demais origens: a cobrança de assinatura
    # é dinheiro que saiu, e cancelar a assinatura não desfaz os meses pagos.
    # O que some é o vínculo; a transação fica, e a competência continua nela
    # dizendo de que mês ela era.
    subscription = models.ForeignKey(Subscription, on_delete=models.SET_NULL, related_name='transactions', blank=True, null=True, verbose_name='Assinatura')
    reference = models.DateField(blank=True, null=True, verbose_name='Competência')

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
    #
    # Assinatura fica de fora, e não por esquecimento: as outras origens mandam
    # nos valores do que geraram — mexer numa parcela sozinha desencontraria o
    # parcelamento. A assinatura não manda: ela lança a cobrança do mês e a
    # solta. O mês em que o preço veio diferente se corrige na própria linha, e
    # o mês que não foi cobrado se apaga sem que a assinatura precise acabar.
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
        constraints = [
            # Uma competência, uma cobrança. O `last_reference` da assinatura já
            # evita a repetição; isto é o que segura o caso em que duas
            # gerações correm ao mesmo tempo e a trava de linha não alcança —
            # numa réplica, num deploy com dois processos subindo juntos.
            models.UniqueConstraint(
                fields=['subscription', 'reference'],
                condition=models.Q(subscription__isnull=False),
                name='unique_subscription_reference',
            ),
        ]


class Conversation(models.Model):
    """Uma conversa do usuário com o assistente.

    O histórico fica no banco, e não na sessão, por duas razões. A conversa
    sobrevive a restart e a deploy, que num sistema que roda em container
    acontecem no meio de qualquer tarde; e o que o assistente respondeu antes de
    um lançamento ser confirmado continua legível depois — se um valor saiu
    errado, dá para ler onde ele veio.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversations', verbose_name='Usuário')
    title = models.CharField(max_length=120, blank=True, verbose_name='Título')
    created = models.DateTimeField(auto_now_add=True, verbose_name='Criada em')
    updated = models.DateTimeField(auto_now=True, verbose_name='Atualizada em')

    def __str__(self):
        return self.title or f'Conversa de {self.created:%d/%m/%Y %H:%M}'

    class Meta:
        ordering = ['-updated']
        verbose_name = 'Conversa'
        verbose_name_plural = 'Conversas'

        # A permissão mora aqui porque o Django precisa pendurá-la em algum
        # modelo, e este é o que só existe por causa do assistente. Ela guarda o
        # botão flutuante e as rotas do chat: sem ela, o botão não é renderizado
        # e as views respondem 403.
        permissions = [
            ('use_assistant', 'Pode usar o assistente'),
        ]


class Role(models.TextChoices):
    USER = 'user', 'Usuário'
    ASSISTANT = 'assistant', 'Assistente'
    TOOL = 'tool', 'Ferramenta'


class Message(models.Model):
    """Um trecho da conversa: o que a tela mostra e o que o modelo relê.

    Os dois não são a mesma coisa, e por isso são campos diferentes. `content` é
    texto para o usuário. `items` é a lista de itens exatamente como a API os
    devolveu e os espera de volta — texto, chamada de ferramenta e, num modelo de
    raciocínio, os itens de raciocínio que sustentam a chamada.

    Guardar os itens crus, e não uma tradução deles, é o que mantém o raciocínio
    inteiro entre uma rodada de ferramenta e a seguinte: reescrevê-los num formato
    próprio significaria descartar o que não coubesse no formato, e o que não
    couber é justamente o que o modelo usaria para não repetir a consulta.

    Uma linha guarda um turno inteiro, e não um item por linha, porque os itens de
    um turno precisam voltar juntos e na ordem: um raciocínio separado da chamada
    que ele justifica faz a API recusar a conversa toda.

    O que NÃO é guardado é o system prompt: ele é remontado a cada requisição,
    porque carrega a data de hoje. Um prompt gravado envelheceria junto com a
    conversa, e "este mês" passaria a significar o mês em que ela começou.
    """

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages', verbose_name='Conversa')
    role = models.CharField(max_length=20, choices=Role.choices, verbose_name='Papel')
    content = models.TextField(blank=True, verbose_name='Conteúdo')

    items = models.JSONField(default=list, blank=True, verbose_name='Itens da Conversa')

    # Nem tudo que o modelo precisa ler é coisa que o usuário precisa ver. O
    # resultado de uma confirmação é o exemplo: o modelo tem de saber que o
    # lançamento foi gravado, senão oferece registrar de novo o que acabou de
    # ser registrado — mas quem clicou no botão não precisa que a própria ação
    # dele seja narrada de volta, em terceira pessoa, como se ele a tivesse
    # digitado.
    visible = models.BooleanField(default=True, verbose_name='Aparece no Chat')

    created = models.DateTimeField(auto_now_add=True, verbose_name='Criada em')

    def __str__(self):
        return f'{self.get_role_display()}: {self.content[:60]}'

    class Meta:
        ordering = ['created', 'id']
        verbose_name = 'Mensagem'
        verbose_name_plural = 'Mensagens'


class PendingWrite(models.Model):
    """Um lançamento montado pelo assistente, à espera do usuário confirmar.

    É o que separa "o modelo propôs" de "o dinheiro foi gravado". A ferramenta de
    registro valida o lançamento no mesmo formulário da tela e para aqui; quem
    grava é o clique, num POST próprio, com CSRF e com o usuário da sessão.

    O registro também é o que impede o replay: ele é consumido na primeira
    confirmação e carrega o dono, então confirmar o pendente de outra pessoa não
    é uma checagem que alguém possa esquecer de escrever — é um filtro que não
    encontra linha nenhuma.
    """

    # Uma proposta velha não deve poder ser confirmada: entre montá-la e clicar,
    # o saldo mudou, a fatura virou, e o usuário já não lembra do que se tratava.
    EXPIRY = timedelta(hours=1)

    class Status(models.TextChoices):
        PENDING = 'pending', 'Aguardando confirmação'
        CONFIRMED = 'confirmed', 'Confirmado'
        CANCELLED = 'cancelled', 'Cancelado'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pending_writes', verbose_name='Usuário')
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='pending_writes', verbose_name='Conversa')

    kind = models.CharField(max_length=20, verbose_name='Tipo de Lançamento')

    # O que vai para o formulário na confirmação, exatamente como ele validou na
    # proposta. Regravar a partir do texto do chat abriria espaço para o valor
    # confirmado ser diferente do valor mostrado.
    payload = models.JSONField(verbose_name='Dados do Lançamento')

    # O que a tela mostra no cartão: rótulos já resolvidos, para o front não
    # precisar consultar conta, categoria e cartão de novo só para escrever.
    summary = models.JSONField(verbose_name='Resumo Exibido')

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name='Situação')
    created = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    resolved = models.DateTimeField(blank=True, null=True, verbose_name='Resolvido em')

    # O que a confirmação gerou. Não é ForeignKey porque o alvo é um de três
    # modelos, e uma chave genérica custaria uma tabela de contenttypes para
    # guardar o que só se usa como rótulo na tela.
    created_label = models.CharField(max_length=200, blank=True, verbose_name='Resultado')

    @property
    def is_expired(self):
        return timezone.now() - self.created > self.EXPIRY

    @property
    def is_open(self):
        return self.status == self.Status.PENDING and not self.is_expired

    def __str__(self):
        return f'{self.get_status_display()}: {self.kind} ({self.user})'

    class Meta:
        ordering = ['-created']
        verbose_name = 'Lançamento Pendente'
        verbose_name_plural = 'Lançamentos Pendentes'


def attachment_path(instance, filename):
    """O caminho do arquivo no disco: pasta do dono, nome sorteado.

    O nome que veio do navegador não entra nisto. Ele é escolhido por quem
    envia, e nome de quem envia virando caminho no servidor é como uma foto de
    nota fiscal acaba gravada por cima de outra coisa. O que fica é um sorteio
    com a extensão que a inspeção do conteúdo confirmou.

    A pasta é a do usuário porque o arquivo é dele: separado assim, uma listagem
    de diretório já responde de quem é cada comprovante, e uma remoção de conta
    tem uma pasta para apagar em vez de uma busca para fazer.
    """
    return f'assistant/{instance.message.conversation.user_id}/{uuid4().hex}{Path(filename).suffix}'


class Attachment(models.Model):
    """A foto ou o áudio que o usuário mandou junto de uma mensagem.

    O arquivo fica em disco, e não no banco, e o que a mensagem guarda é uma
    referência a esta linha. São dois motivos. Uma foto de nota fiscal em base64
    dentro do JSON do turno voltaria para a API a cada mensagem seguinte da
    conversa, e engordaria o backup do Postgres com bytes que não são dado
    financeiro; e a miniatura que reaparece no chat depois de um F5 precisa de
    uma URL, que uma coluna JSON não tem como servir.

    Um anexo por mensagem: o compositor manda um arquivo de cada vez, e uma
    relação de um para um deixa isso explícito no schema em vez de deixá-lo como
    combinado entre o front e a view.

    O áudio não é lido pelo modelo. Ele é transcrito na chegada, e o que vai para
    a conversa é a transcrição — que fica em `content`, na mensagem. O arquivo
    permanece para o usuário poder ouvir de novo o que ele mesmo ditou, e para
    conferir a transcrição quando um número parecer errado.
    """

    class Kind(models.TextChoices):
        IMAGE = 'image', 'Imagem'
        AUDIO = 'audio', 'Áudio'

    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='attachment', verbose_name='Mensagem')
    kind = models.CharField(max_length=10, choices=Kind.choices, verbose_name='Tipo')
    file = models.FileField(upload_to=attachment_path, max_length=200, verbose_name='Arquivo')

    # O tipo confirmado pela inspeção do conteúdo, e não o rótulo que o
    # navegador mandou. É ele que sai no Content-Type de quem for baixar.
    mime = models.CharField(max_length=60, verbose_name='Tipo de Conteúdo')

    created = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')

    def __str__(self):
        return f'{self.get_kind_display()} de {self.message.conversation.user}'

    class Meta:
        verbose_name = 'Anexo'
        verbose_name_plural = 'Anexos'


@receiver(post_delete, sender=Attachment)
def remove_attachment_file(sender, instance, **kwargs):
    """Apaga o arquivo quando a linha sai.

    O Django não faz isso sozinho, e aqui a diferença importa: "Limpar a
    conversa" apaga a conversa em cascata, e sem isto os comprovantes ficariam
    no volume para sempre — o usuário teria mandado apagar e o disco continuaria
    guardando a foto dos gastos dele.
    """
    if instance.file:
        instance.file.delete(save=False)
