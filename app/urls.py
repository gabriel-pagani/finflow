from django.urls import path
from . import views


app_name = 'app'

urlpatterns = [
    path('', views.OverviewView.as_view(), name='overview'),
    path('prevision/', views.ForecastView.as_view(), name='forecast'),
    path('transactions/', views.TransactionListView.as_view(), name='transaction_list'),
]
