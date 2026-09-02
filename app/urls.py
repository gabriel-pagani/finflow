from django.urls import path
from . import views
from .assistant import views as assistant_views


app_name = 'app'

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('', views.OverviewView.as_view(), name='overview'),
    path('prevision/', views.ForecastView.as_view(), name='forecast'),
    path('transactions/', views.TransactionsListView.as_view(), name='transactions_list'),
    path('transaction/add/', views.TransactionCreateView.as_view(), name='transaction_create'),
    path('transaction/<int:pk>/change/', views.TransactionUpdateView.as_view(), name='transaction_update'),
    path('transaction/<int:pk>/delete/', views.TransactionDeleteView.as_view(), name='transaction_delete'),
    path('installment/add/', views.InstallmentCreateView.as_view(), name='installment_create'),
    path('transfer/add/', views.TransferCreateView.as_view(), name='transfer_create'),
    path('cards/', views.CardsListView.as_view(), name='cards_list'),
    path('card/add/', views.CardCreateView.as_view(), name='card_create'),
    path('card/<int:pk>/change/', views.CardUpdateView.as_view(), name='card_update'),
    path('card/<int:pk>/delete/', views.CardDeleteView.as_view(), name='card_delete'),

    # Chat do assistente. O stream é POST lido por fetch, e não EventSource,
    # porque a pergunta precisa ir no corpo e o CSRF num cabeçalho. Todas as
    # rotas exigem a permissão app.use_assistant.
    path('assistant/stream/', assistant_views.StreamView.as_view(), name='assistant_stream'),
    path('assistant/history/', assistant_views.HistoryView.as_view(), name='assistant_history'),
    path('assistant/reset/', assistant_views.ResetView.as_view(), name='assistant_reset'),
    path('assistant/pending/<int:pk>/confirm/', assistant_views.ConfirmView.as_view(), name='assistant_confirm'),
    path('assistant/pending/<int:pk>/cancel/', assistant_views.CancelView.as_view(), name='assistant_cancel'),
]
