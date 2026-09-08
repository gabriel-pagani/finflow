from django.contrib import admin
from reversion.admin import VersionAdmin

from app.models import BusinessRule


@admin.register(BusinessRule)
class BusinessRuleAdmin(VersionAdmin):
    list_display = ('account', 'type', 'method')
    list_filter = ('type', 'method', 'account')
    search_fields = ('account__description',)
    autocomplete_fields = ('account',)
    list_select_related = ('account',)
