"""As telas do sistema, divididas por assunto e reexportadas aqui.

O urls.py continua escrevendo `views.OverviewView`, e os testes continuam
importando de `app.views`: a divisão em módulos é interna ao pacote.
"""

from .auth import LoginView, LogoutView
from .dashboards import ForecastView, OverviewView
from .transactions import (
    InstallmentCreateView,
    TransactionCreateView,
    TransactionDeleteView,
    TransactionUpdateView,
    TransactionsListView,
    TransferCreateView,
)
from .cards import CardCreateView, CardDeleteView, CardUpdateView, CardsListView
from .subscriptions import (
    SubscriptionCreateView,
    SubscriptionDeleteView,
    SubscriptionUpdateView,
    SubscriptionsListView,
)


__all__ = [
    'CardCreateView',
    'CardDeleteView',
    'CardUpdateView',
    'CardsListView',
    'ForecastView',
    'InstallmentCreateView',
    'LoginView',
    'LogoutView',
    'OverviewView',
    'SubscriptionCreateView',
    'SubscriptionDeleteView',
    'SubscriptionUpdateView',
    'SubscriptionsListView',
    'TransactionCreateView',
    'TransactionDeleteView',
    'TransactionUpdateView',
    'TransactionsListView',
    'TransferCreateView',
]
