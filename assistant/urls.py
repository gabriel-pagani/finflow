from django.urls import path

from . import views


app_name = 'assistant'

urlpatterns = [
    path('', views.PageView.as_view(), name='page'),
    path('stream/', views.StreamView.as_view(), name='stream'),
    path('history/', views.HistoryView.as_view(), name='history'),
    path('reset/', views.ResetView.as_view(), name='reset'),
    path('attachment/<int:pk>/', views.AttachmentView.as_view(), name='attachment'),
    path('proposal/<int:pk>/confirm/', views.ConfirmView.as_view(), name='confirm'),
    path('proposal/<int:pk>/cancel/', views.CancelView.as_view(), name='cancel'),
    path('commands/', views.CommandsView.as_view(), name='commands'),
    path('command/add/', views.CommandWriteView.as_view(), name='command_create'),
    path('command/<int:pk>/change/', views.CommandWriteView.as_view(), name='command_update'),
    path('command/<int:pk>/delete/', views.CommandDeleteView.as_view(), name='command_delete'),
]
