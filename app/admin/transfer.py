from django.contrib import admin
from reversion.admin import VersionAdmin

from app.models import Transaction, Transfer


class LegInline(admin.TabularInline):
    model = Transaction
    fields = ('account', 'type', 'method', 'value', 'effective_at')
    readonly_fields = fields
    ordering = ('id',)
    extra = 0
    can_delete = False
    verbose_name = 'Perna'
    verbose_name_plural = 'Pernas'

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Transfer)
class TransferAdmin(VersionAdmin):
    list_display = ('user', 'origin', 'destination', 'description', 'value', 'occurred_at')
    list_filter = ('origin', 'destination', 'occurred_at')
    search_fields = ('description', 'origin__description', 'destination__description', 'user__username')
    autocomplete_fields = ('user', 'origin', 'destination')
    list_select_related = ('origin', 'destination', 'user')
    date_hierarchy = 'occurred_at'
    ordering = ('-occurred_at', '-id')
    readonly_fields = ('created_at', 'updated_at')
    inlines = (LegInline,)
