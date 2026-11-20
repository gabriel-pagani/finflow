"""O que vem do banco pela Pluggy, à espera de revisão antes de virar lançamento."""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from .account import Account
from .choices import Method, Type
from .installment import Installment
from .transaction import Transaction


class BankConnection(models.Model):
    # Com o MeuPluggy a API não lista as conexões: o id é copiado do painel da
    # Pluggy (Demo → Connected Items) e cadastrado uma vez no admin.
    STALE_AFTER = timedelta(minutes=15)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bank_connections', verbose_name='Usuário')
    item_id = models.CharField(max_length=64, unique=True, verbose_name='Id da Conexão na Pluggy')
    # Tudo o que a conexão traz é desta conta, inclusive as compras no cartão.
    # Nula só nas conexões cadastradas antes do campo existir.
    account = models.ForeignKey(Account, on_delete=models.PROTECT, null=True, related_name='bank_connections', verbose_name='Conta')
    # O banco devolve até um ano de histórico, quase todo já lançado à mão:
    # só entra na revisão o que aconteceu a partir daqui.
    since = models.DateField(default=timezone.localdate, verbose_name='Importar a partir de')
    synced_at = models.DateTimeField(blank=True, null=True, verbose_name='Última Busca')

    @property
    def is_stale(self):
        return not self.synced_at or timezone.now() - self.synced_at > self.STALE_AFTER

    def __str__(self):
        return f'{self.account} ({self.item_id})' if self.account else self.item_id

    class Meta:
        ordering = ['account__description', 'item_id']
        verbose_name = 'Conexão Bancária'
        verbose_name_plural = 'Conexões Bancárias'


class BankAccount(models.Model):
    class Kind(models.TextChoices):
        BANK = 'BANK', 'Conta'
        CREDIT = 'CREDIT', 'Cartão'

    connection = models.ForeignKey(BankConnection, on_delete=models.CASCADE, related_name='accounts', verbose_name='Conexão')
    external_id = models.CharField(max_length=64, unique=True, verbose_name='Id na Pluggy')
    kind = models.CharField(max_length=20, choices=Kind.choices, verbose_name='Tipo')
    name = models.CharField(max_length=200, blank=True, verbose_name='Nome')

    def __str__(self):
        return self.name or self.external_id

    class Meta:
        verbose_name = 'Conta Bancária'
        verbose_name_plural = 'Contas Bancárias'


class BankTransaction(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'A revisar'
        APPROVED = 'APPROVED', 'Lançada'
        DISCARDED = 'DISCARDED', 'Descartada'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bank_transactions', verbose_name='Usuário')
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name='transactions', verbose_name='Conta Bancária')
    external_id = models.CharField(max_length=64, unique=True, verbose_name='Id na Pluggy')
    type = models.CharField(max_length=20, choices=Type.choices, verbose_name='Tipo')
    method = models.CharField(max_length=20, choices=Method.choices, verbose_name='Método')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor')
    occurred_at = models.DateField(verbose_name='Data')
    # O horário que o banco registrou: ordena as transações do mesmo dia.
    moment = models.DateTimeField(blank=True, null=True, verbose_name='Data e Hora no Banco')
    description = models.CharField(max_length=200, blank=True, verbose_name='Descrição')
    bank_category = models.CharField(max_length=100, blank=True, verbose_name='Categoria no Banco')
    card_digits = models.CharField(max_length=4, blank=True, verbose_name='Final do Cartão')

    # Compra parcelada: o banco manda cada parcela, inclusive as futuras. Todas
    # da mesma compra dividem a purchase_key e viram um único parcelamento.
    parcel = models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Parcela')
    parcels = models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Número de Parcelas')
    purchase_date = models.DateField(blank=True, null=True, verbose_name='Data da Compra')
    purchase_key = models.CharField(max_length=255, blank=True, db_index=True, verbose_name='Chave da Compra')

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name='Situação')
    transaction = models.ForeignKey(Transaction, on_delete=models.SET_NULL, blank=True, null=True, related_name='bank_transactions', verbose_name='Transação')
    installment = models.ForeignKey(Installment, on_delete=models.SET_NULL, blank=True, null=True, related_name='bank_transactions', verbose_name='Parcelamento')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')

    def group(self):
        """As linhas a revisar que saem junto: as parcelas da mesma compra, ou só esta."""
        rows = BankTransaction.objects.filter(status=self.Status.PENDING, user_id=self.user_id)
        if self.purchase_key:
            return rows.filter(bank_account_id=self.bank_account_id, purchase_key=self.purchase_key)
        return rows.filter(pk=self.pk)

    def __str__(self):
        return f'{self.occurred_at:%d/%m/%Y} {self.description} ({self.value})'

    class Meta:
        ordering = ['-occurred_at', '-moment', '-id']
        indexes = [models.Index(fields=['user', 'status'])]
        verbose_name = 'Transação Bancária'
        verbose_name_plural = 'Transações Bancárias'
