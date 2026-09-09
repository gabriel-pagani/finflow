from calendar import monthrange
from datetime import date

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Func
from django.utils import timezone

from ..utils.dates import add_months


class DateRange(Func):
    function = 'DATERANGE'
    output_field = DateRangeField()


class SubscriptionPeriod(models.Model):
    subscription = models.ForeignKey('app.Subscription', on_delete=models.CASCADE, related_name='periods', verbose_name='Assinatura')
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
