from django.urls import path
from . import views


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
]
