from django.contrib import admin
from reversion.admin import VersionAdmin

from app.models import Account


@admin.register(Account)
class AccountAdmin(VersionAdmin):
    list_display = ('description',)
    search_fields = ('description',)
