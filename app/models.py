from decimal import Decimal, ROUND_DOWN
from django.conf import settings
from django.db import models
from django.contrib.auth.models import AbstractUser, Group as BaseGroup
from django.core.exceptions import ValidationError


def add_months(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])

    return dt.replace(year=year, month=month, day=day)


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


class Installment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='installments', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, verbose_name='Conta')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, verbose_name='Categoria')
    description = models.CharField(max_length=200, blank=True, null=True, verbose_name='Descrição')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor Total')
    installments = models.PositiveSmallIntegerField(verbose_name='Número de Parcelas')
    datetime = models.DateTimeField(verbose_name='Data e Hora')

    TYPE = Type.OUT
    METHOD = Method.CREDIT

    def clean(self):
        super().clean()
        if self.account_id:
            if not BusinessRule.objects.filter(account=self.account, type=self.TYPE, method=self.METHOD).exists():
                raise ValidationError(f'A conta não permite {Type(self.TYPE).label.lower()} em {Method(self.METHOD).label}, necessário para registrar as parcelas.')
        if self.installments is not None and self.installments < 2:
            raise ValidationError({'installments': 'Um parcelamento deve ter no mínimo 2 parcelas.'})

    def generate_transactions(self):
        self.transactions.all().delete()

        base_value = (self.value / self.installments).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        last_value = self.value - base_value * (self.installments - 1)

        transactions = []
        for i in range(self.installments):
            parcel_value = last_value if i == self.installments - 1 else base_value
            transactions.append(Transaction(
                user=self.user,
                account=self.account,
                type=self.TYPE,
                method=self.METHOD,
                category=self.category,
                description=self.description,
                value=parcel_value,
                datetime=add_months(self.datetime, i),
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


class Transaction(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='transactions_owned', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, verbose_name='Conta')
    type = models.CharField(max_length=20, choices=Type.choices, verbose_name='Tipo')
    method = models.CharField(max_length=20, choices=Method.choices, verbose_name='Método')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, verbose_name='Categoria')
    description = models.CharField(max_length=200, blank=True, null=True, verbose_name='Descrição')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor')
    datetime = models.DateTimeField(verbose_name='Data e Hora')

    installment = models.ForeignKey(Installment, on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Parcelamento')
    parcel = models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Parcela')

    investment = models.ForeignKey(Investment, on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Investimento')
    contribution = models.ForeignKey(Contribution, on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Aplicação')
    redemption = models.ForeignKey(Redemption, on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Resgate')

    def clean(self):
        super().clean()
        if self.account_id and self.type and self.method:
            if not BusinessRule.objects.filter(account=self.account, type=self.type, method=self.method).exists():
                raise ValidationError('Combinação de conta, tipo e método não permitida pelas regras de negócio.')

    @property
    def is_derived(self):
        return bool(self.installment_id or self.investment_id)

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    def __str__(self):
        if self.installment_id:
            return f'{self.category_display} (R${self.value}) - {self.parcel}/{self.installment.installments}'
        if self.redemption_id:
            return f'{self.category_display} (R${self.value}) - Resgate'
        if self.contribution_id:
            return f'{self.category_display} (R${self.value}) - Aplicação'
        return f'{self.category_display} (R${self.value})'

    class Meta:
        ordering = ['-datetime']
        verbose_name = 'Transação'
        verbose_name_plural = 'Transações'
