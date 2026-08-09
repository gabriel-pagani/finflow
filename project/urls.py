from django.contrib import admin
from django.urls import path, include
from django.conf import settings

admin.site.site_header = "Portal de Administração"
admin.site.site_title = "Sistema"
admin.site.index_title = "Painel de Controle"

urlpatterns = [
    # path('', include('app.urls', namespace='app')),
    path(F'{settings.ADMIN_PANEL_PATH}/', admin.site.urls),
]
