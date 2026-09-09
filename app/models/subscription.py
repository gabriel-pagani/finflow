from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

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

    def clean(self):
        super().clean()
        if self.account_id and not BusinessRule.objects.filter(account=self.account, type=self.TYPE, method=self.METHOD).exists():
            raise ValidationError({'account': f'A conta não permite {self.TYPE.label.lower()} em {self.METHOD.label}, necessário para registrar as cobranças da assinatura.'})
        if self.card_id:
            if self.account_id and self.card.account_id != self.account_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outra conta.'})
            if self.user_id and self.card.user_id != self.user_id:
                raise ValidationError({'card': 'O cartão escolhido pertence a outro usuário.'})

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
