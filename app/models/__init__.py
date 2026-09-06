"""Os modelos do sistema, divididos por assunto e reexportados aqui.

O pacote substituiu um models.py de mil e cem linhas, mas nada fora dele
precisou mudar: `from app.models import Transaction` continua sendo o import,
e é isto que este arquivo garante. As migrações antigas também dependem disso —
a 0017 referencia `app.models.attachment_path` pelo nome.

A ordem dos imports abaixo é a das dependências, e não alfabética: cada módulo
só enxerga os anteriores. Quem gera transação (investimento, assinatura) importa
`transactions`; a transação alcança esses dois por referência em string. É o que
mantém o grafo sem ciclo.
"""

from .choices import Method, Nature, RECURRENCE_MONTHS, Recurrence, Role, Type
from .accounts import Account, BusinessRule, Category, Group, User
from .cards import Card
from .transactions import Installment, Transaction, Transfer
from .investments import Contribution, Investment, InvestmentEntry, Redemption, Yield
from .subscriptions import Subscription
from .assistant import Attachment, Conversation, Message, PendingWrite, attachment_path, remove_attachment_file


__all__ = [
    'Account',
    'Attachment',
    'BusinessRule',
    'Card',
    'Category',
    'Contribution',
    'Conversation',
    'Group',
    'Installment',
    'Investment',
    'InvestmentEntry',
    'Message',
    'Method',
    'Nature',
    'PendingWrite',
    'RECURRENCE_MONTHS',
    'Recurrence',
    'Redemption',
    'Role',
    'Subscription',
    'Transaction',
    'Transfer',
    'Type',
    'User',
    'Yield',
    'attachment_path',
    'remove_attachment_file',
]
