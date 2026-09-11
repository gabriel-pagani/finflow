from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Func
from django.utils import timezone

from ..utils.dates import add_months
from ..utils.formatting import format_to_money
from .account import Account
from .business_rule import BusinessRule
from .card import Card
from .category import Category
from .choices import Method, Nature, Recurrence, Type
from .transaction import Transaction


class Subscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscriptions', verbose_name='Usuário')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='subscriptions', verbose_name='Conta')
    card = models.ForeignKey(Card, on_delete=models.RESTRICT, related_name='subscriptions', verbose_name='Cartão')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True, related_name='subscriptions', verbose_name='Categoria')
    description = models.CharField(max_length=200, verbose_name='Assinatura', help_text='O nome do serviço. Ex.: Spotify, Netflix, Claude.')
    value = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))], verbose_name='Valor da Cobrança')
    recurrence = models.PositiveSmallIntegerField(choices=Recurrence.choices, default=Recurrence.MONTHLY, verbose_name='Recorrência')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Data e Hora da Atualização')

    TYPE = Type.OUT
    METHOD = Method.CREDIT
    NATURE = Nature.REGULAR

    LOCKED_AFTER_CHARGES = ('user', 'account', 'card', 'category', 'description', 'recurrence')

    def clean(self):
        super().clean()
        if self.account_id and not BusinessRule.objects.filter(account=self.account, type=self.TYPE, method=self.METHOD).exists():
            raise ValidationError({'account': f'A conta não permite {self.TYPE.label.lower()} em {self.METHOD.label}, necessário para registrar as cobranças da assinatura.'})
        if self.card_id:
            if self.account_id and self.card.account_id != self.account_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outra conta.'})
            if self.user_id and self.card.user_id != self.user_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outro usuário.'})
        erros = {
            campo: 'Com cobranças lançadas, este campo não pode mais ser alterado.'
            for campo in self.changed_after_charges()
        }
        if erros:
            raise ValidationError(erros)

    def changed_after_charges(self):
        if not self.pk or not self.transactions.exists():
            return []

        anterior = Subscription.objects.get(pk=self.pk)

        return [
            campo for campo in self.LOCKED_AFTER_CHARGES
            if getattr(self, self._meta.get_field(campo).attname) != getattr(anterior, self._meta.get_field(campo).attname)
        ]

    @property
    def interval(self):
        return self.recurrence

    @property
    def current_period(self):
        return next((period for period in self.periods.all() if period.is_active), None)

    @property
    def is_active(self):
        return self.current_period is not None

    @property
    def last_reference(self):
        return self.transactions.aggregate(models.Max('reference'))['reference__max']

    def due_charge_dates(self, today=None):
        today = today or timezone.localdate()

        return [data for period in self.periods.all() for data in period.due_charge_dates(self.interval, today)]

    def generate_charges(self, today=None):
        # As competências já gravadas vêm numa consulta só: sem isso cada data
        # devida custaria um get_or_create, inclusive as de períodos antigos.
        existentes = set(self.transactions.values_list('reference', flat=True))

        charges = []
        for charge_on in self.due_charge_dates(today):
            reference = charge_on.replace(day=1)
            if reference in existentes:
                continue

            charge, created = Transaction.objects.get_or_create(
                subscription=self,
                reference=reference,
                defaults={
                    'user': self.user,
                    'account': self.account,
                    'card': self.card,
                    'type': self.TYPE,
                    'method': self.METHOD,
                    'nature': self.NATURE,
                    'category': self.category,
                    'description': self.description,
                    'value': self.value,
                    'occurred_at': charge_on,
                },
            )
            existentes.add(reference)
            if created:
                charges.append(charge)

        return charges

    @classmethod
    def generate_due(cls, user, today=None):
        today = today or timezone.localdate()

        # Filtrar por quem já tem cobrança no mês corrente pularia período antigo
        # acrescentado depois; o generate_charges já sai barato quando nada vence.
        pending = cls.objects.filter(user=user).select_related('card', 'account', 'category').prefetch_related('periods')

        return sum(len(subscription.generate_charges(today)) for subscription in pending)

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    def __str__(self):
        return f'{self.description} ({format_to_money(self.value)} · {self.get_recurrence_display()})'

    class Meta:
        ordering = ['description']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(value__gte=Decimal('0.01')),
                name='subscription_value_positive',
                violation_error_message='O valor deve ser maior que zero.',
            ),
            models.CheckConstraint(
                condition=models.Q(recurrence__in=Recurrence.values),
                name='subscription_recurrence_within_choices',
                violation_error_message='A recorrência precisa ser uma das opções previstas.',
            ),
        ]
        verbose_name = 'Assinatura'
        verbose_name_plural = 'Assinaturas'


class DateRange(Func):
    function = 'DATERANGE'
    output_field = DateRangeField()


class SubscriptionPeriod(models.Model):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name='periods', verbose_name='Assinatura')
    started_at = models.DateField(verbose_name='Data da Primeira Cobrança', help_text='A data da primeira cobrança deste período. O dia dela é o dia usado nas cobranças seguintes.')
    cancelled_at = models.DateField(blank=True, null=True, verbose_name='Data do Encerramento', help_text='Deixe em branco enquanto o período estiver ativo. A cobrança que cai até esta data ainda é gerada.')

    def clean(self):
        super().clean()
        if self.started_at and self.cancelled_at and self.cancelled_at < self.started_at:
            raise ValidationError({'cancelled_at': 'O encerramento não pode ser anterior à primeira cobrança do período.'})
        if self.subscription_id and self.started_at:
            irmaos = SubscriptionPeriod.objects.filter(subscription_id=self.subscription_id).exclude(pk=self.pk)
            if irmaos.filter(cancelled_at__isnull=True).exists() and self.cancelled_at is None:
                raise ValidationError('A assinatura já tem um período em aberto. Encerre o anterior antes de abrir outro.')
            for irmao in irmaos:
                if self.overlaps(irmao):
                    raise ValidationError({'started_at': f'Este período se sobrepõe ao que começou em {irmao.started_at:%d/%m/%Y}.'})
        if self.pk:
            anterior = SubscriptionPeriod.objects.get(pk=self.pk)
            if self.started_at != anterior.started_at and anterior.charges().exists():
                raise ValidationError({'started_at': 'Com cobranças lançadas, a data da primeira cobrança não pode mais ser alterada.'})
            ultima = anterior.charges().aggregate(models.Max('occurred_at'))['occurred_at__max']
            if self.cancelled_at and ultima and self.cancelled_at < ultima:
                raise ValidationError({'cancelled_at': f'A última cobrança deste período caiu em {ultima:%d/%m/%Y}. O encerramento não pode ser anterior a ela.'})

    def delete(self, *args, **kwargs):
        if self.has_charges():
            raise ValidationError('Com cobranças lançadas, o período não pode mais ser apagado.')
        return super().delete(*args, **kwargs)

    def has_charges(self):
        gravado = SubscriptionPeriod.objects.filter(pk=self.pk).first() if self.pk else None

        return gravado is not None and gravado.charges().exists()

    def charges(self):
        charges = self.subscription.transactions.filter(occurred_at__gte=self.started_at)
        if self.cancelled_at:
            charges = charges.filter(occurred_at__lte=self.cancelled_at)

        return charges

    def overlaps(self, other):
        fim = self.cancelled_at or date.max
        fim_outro = other.cancelled_at or date.max

        return self.started_at <= fim_outro and other.started_at <= fim

    @property
    def is_active(self):
        return self.cancelled_at is None

    @property
    def charge_day(self):
        return self.started_at.day

    def charge_date(self, reference):
        return date(reference.year, reference.month, min(self.charge_day, monthrange(reference.year, reference.month)[1]))

    def due_charge_dates(self, interval, today=None):
        today = today or timezone.localdate()
        # Encerrar não apaga o que já venceu: a cobrança que cai até o dia do
        # encerramento ainda é gerada, e a geração para ali.
        limit = min(today, self.cancelled_at) if self.cancelled_at else today

        reference = self.started_at.replace(day=1)
        dates = []
        # Quem avança é a competência, e a data é recalculada a cada passo: somar
        # meses na própria data encurtaria o dia 31 em fevereiro e ele nunca voltaria.
        while (charge_on := self.charge_date(reference)) <= limit:
            dates.append(charge_on)
            reference = add_months(reference, interval)

        return dates

    def __str__(self):
        if self.cancelled_at:
            return f'{self.started_at:%d/%m/%Y} até {self.cancelled_at:%d/%m/%Y}'

        return f'{self.started_at:%d/%m/%Y} até hoje'

    class Meta:
        ordering = ['subscription__description', 'started_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cancelled_at__isnull=True) | models.Q(cancelled_at__gte=models.F('started_at')),
                name='subscription_period_cancelled_after_start',
                violation_error_message='O encerramento não pode ser anterior à primeira cobrança do período.',
            ),
            models.UniqueConstraint(
                fields=['subscription'],
                condition=models.Q(cancelled_at__isnull=True),
                name='subscription_period_single_open',
                violation_error_message='A assinatura já tem um período em aberto.',
            ),
            models.UniqueConstraint(
                fields=['subscription', 'started_at'],
                name='subscription_period_unique_start',
                violation_error_message='A assinatura já tem um período que começa nessa data.',
            ),
            ExclusionConstraint(
                name='subscription_period_no_overlap',
                expressions=[
                    (DateRange('started_at', 'cancelled_at', RangeBoundary(inclusive_upper=True)), RangeOperators.OVERLAPS),
                    ('subscription', RangeOperators.EQUAL),
                ],
                violation_error_message='Este período se sobrepõe a outro da mesma assinatura.',
            ),
        ]
        verbose_name = 'Período'
        verbose_name_plural = 'Períodos'
