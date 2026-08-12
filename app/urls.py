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
]
