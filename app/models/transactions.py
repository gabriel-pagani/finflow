"""A transação e os dois registros de origem que a geram sem sair daqui.

Transação, parcelamento e transferência ficam no mesmo módulo porque se
referenciam nos dois sentidos: a transação aponta para a origem por chave
estrangeira, e a origem gera as transações filhas. Separá-los custaria um
import dentro de método em cada lado para nada — eles mudam juntos.

As origens que moram em outros módulos (investimento e assinatura) entram como
referência por string. É o que deixa este módulo ser importado por eles, e não
o contrário: quem gera transação importa a transação, sempre nessa direção.
"""

from decimal import Decimal, ROUND_DOWN

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from ..utils.dates import add_months
from ..utils.formatting import format_money
from .accounts import Account, BusinessRule, Category
from .cards import Card
from .choices import Method, Nature, Type


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
        return f'{format_money(self.value)} ({self.installments}x)'

    class Meta:
        ordering = ['-datetime']
        verbose_name = 'Parcelamento'
        verbose_name_plural = 'Parcelamentos'


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
        return f'{format_money(self.value)} ({self.origin} → {self.destination})'

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

    investment = models.ForeignKey('app.Investment', on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Investimento')
    contribution = models.ForeignKey('app.Contribution', on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Aplicação')
    redemption = models.ForeignKey('app.Redemption', on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Resgate')

    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name='transactions', blank=True, null=True, verbose_name='Transferência')

    # SET_NULL, e não CASCADE como as demais origens: a cobrança de assinatura
    # é dinheiro que saiu, e cancelar a assinatura não desfaz os meses pagos.
    # O que some é o vínculo; a transação fica, e a competência continua nela
    # dizendo de que mês ela era.
    subscription = models.ForeignKey('app.Subscription', on_delete=models.SET_NULL, related_name='transactions', blank=True, null=True, verbose_name='Assinatura')
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
            return f'{self.category_display} ({format_money(self.value)}) - {self.parcel}/{self.installment.installments}'
        if self.redemption_id:
            return f'{self.category_display} ({format_money(self.value)}) - Resgate'
        if self.contribution_id:
            return f'{self.category_display} ({format_money(self.value)}) - Aplicação'
        if self.transfer_id:
            sentido = 'Envio' if self.type == Type.OUT else 'Recebimento'
            return f'{self.category_display} ({format_money(self.value)}) - Transferência ({sentido})'
        return f'{self.category_display} ({format_money(self.value)})'

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
