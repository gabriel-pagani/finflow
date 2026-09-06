"""O cartão de crédito e o ciclo de fatura que decide a data de cada compra."""

from calendar import monthrange
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from ..utils.dates import add_months, next_business_day
from .accounts import Account, BusinessRule
from .choices import Method, Type


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
