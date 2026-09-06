"""A assinatura: o molde da cobrança que se repete, e as cobranças vencidas."""

from datetime import date, datetime, time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction as db_transaction
from django.utils import timezone

from ..utils.dates import add_months, current_month
from ..utils.formatting import format_money
from .accounts import Account, BusinessRule, Category
from .cards import Card
from .choices import Method, RECURRENCE_MONTHS, Recurrence, Type
from .transactions import Transaction


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

    O que a assinatura guarda do passado é só o mês de referência, e ele não é
    data: é a fase da recorrência. Serve para a anual paga em janeiro cobrar em
    janeiro, mesmo cadastrada em setembro. O que ficou para trás não vira
    lançamento — nunca foi responsabilidade do sistema.

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

    # O mês em que a assinatura cobrou pela primeira vez, de 1 a 12. Sem ano, de
    # propósito: ele fixa a fase da recorrência, e para isso janeiro de 2000 e
    # janeiro de 2026 dizem a mesma coisa — a anual cobra em janeiro. Numa
    # mensal ele não muda nada, porque todo mês serve.
    anchor_month = models.PositiveSmallIntegerField(default=current_month, validators=[MinValueValidator(1), MaxValueValidator(12)], verbose_name='Mês da Primeira Cobrança')

    # A última competência lançada, e o que impede a mesma cobrança de sair duas
    # vezes. Nulo enquanto a assinatura não cobrou nenhuma vez.
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

    def next_reference(self, today=None):
        """Competência da próxima cobrança: a que sai assim que o dia dela chegar.

        Depois da primeira, sai do que já foi gerado: a anual que cobrou em
        março cobra em março do ano seguinte. Antes dela, sai do mês de
        referência — a primeira competência daqui para a frente que cai na fase
        certa. Uma anual de janeiro cadastrada em setembro cobra em janeiro do
        ano que vem, e nada antes disso: competência que ficou para trás nunca
        foi responsabilidade do sistema, e inventar a transação dela faria
        aparecer, num mês já fechado, uma saída que ninguém conferiu.
        """
        if self.last_reference:
            return add_months(self.last_reference, self.interval)

        current = (today or timezone.localdate()).replace(day=1)

        # Começa um ano atrás para o passo alcançar o mês corrente vindo de trás,
        # em vez de pular por cima dele.
        reference = date(current.year - 1, self.anchor_month, 1)
        while reference < current:
            reference = add_months(reference, self.interval)

        return reference

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
        lança no dia 10, e não no 1º. Competência anterior ao cadastro nunca
        entra — quem garante isso é `next_reference`, que só olha do mês
        corrente para a frente enquanto a assinatura não cobrou nenhuma vez.
        """
        today = today or timezone.localdate()
        created = []

        # A saída barata, antes de abrir transação e travar a linha: este
        # caminho roda a cada página aberta, e uma assinatura anual passa onze
        # meses do ano sem nada a fazer.
        if self.charge_date(self.next_reference(today)) > today:
            return created

        with db_transaction.atomic():
            # O cron e a tela podem chegar ao mesmo tempo, e o que decide a
            # próxima competência é o que já foi gerado. Sem o lock, os dois
            # leriam a mesma linha e lançariam a mesma cobrança duas vezes.
            locked = Subscription.objects.select_for_update().get(pk=self.pk)

            reference = locked.next_reference(today)
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

    @property
    def category_display(self):
        return str(self.category) if self.category_id else 'Categoria Não Identificada'

    def __str__(self):
        return f'{self.description} ({format_money(self.value)} · {self.get_recurrence_display()})'

    class Meta:
        ordering = ['description']
        verbose_name = 'Assinatura'
        verbose_name_plural = 'Assinaturas'
