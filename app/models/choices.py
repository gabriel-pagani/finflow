from django.db import models


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


class Recurrence(models.IntegerChoices):
    MONTHLY = 1, 'Mensal'
    BIMONTHLY = 2, 'Bimestral'
    QUARTERLY = 3, 'Trimestral'
    SEMIANNUAL = 6, 'Semestral'
    ANNUAL = 12, 'Anual'
