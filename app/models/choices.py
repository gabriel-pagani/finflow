"""Os códigos fixos do domínio, num módulo só.

Eles são referenciados por quase todo modelo, formulário e view; reunidos aqui,
nenhum módulo de modelo precisa importar outro só para alcançar um choice.
"""

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


class Role(models.TextChoices):
    """Quem falou numa mensagem da conversa com o assistente."""

    USER = 'user', 'Usuário'
    ASSISTANT = 'assistant', 'Assistente'
    TOOL = 'tool', 'Ferramenta'
