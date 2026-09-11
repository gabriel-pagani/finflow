from django.contrib import admin
from reversion.admin import VersionAdmin

from app.models import Card


@admin.register(Card)
class CardAdmin(VersionAdmin):
    list_display = ('user', 'account', 'last_digits', 'closing_day', 'due_day')
    list_filter = ('account',)
    search_fields = ('last_digits', 'account__description', 'user__username')
    autocomplete_fields = ('user', 'account')
    list_select_related = ('account', 'user')

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.transactions.exists():
            return Card.LOCKED_AFTER_TRANSACTIONS + tuple(self.readonly_fields)
        return self.readonly_fields
