from django.urls import path
from . import api, views


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

    # API para agentes externos (n8n). Fica sob /api/ e autentica por token, sem
    # sessão: as rotas acima continuam sendo as da interface, com login e CSRF.
    #
    # A consulta é repartida por assunto porque o custo do agente se mede em
    # tokens por mensagem: as regras não mudam entre uma pergunta e outra, e ele
    # não precisa recebê-las de novo a cada saldo consultado.
    path('api/', api.IndexView.as_view(), name='api_index'),
    path('api/documentation/', api.DocumentationView.as_view(), name='api_documentation'),
    path('api/options/', api.OptionsView.as_view(), name='api_options'),
    path('api/analytics/', api.AnalyticsView.as_view(), name='api_analytics'),
    path('api/transactions/', api.TransactionCreateView.as_view(), name='api_transaction_create'),

    # Composição das rotas acima, mantida para o fluxo que já apontava para cá
    # não quebrar no deploy.
    path('api/context/', api.ContextView.as_view(), name='api_context'),
]
