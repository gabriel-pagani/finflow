from django.contrib import admin
from reversion.admin import VersionAdmin

from app.models import Installment, Transaction


class ParcelInline(admin.TabularInline):
    model = Transaction
    fields = ('parcel', 'value', 'effective_at')
    readonly_fields = fields
    ordering = ('parcel',)
    extra = 0
    can_delete = False
    verbose_name = 'Parcela'
    verbose_name_plural = 'Parcelas'

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Installment)
class InstallmentAdmin(VersionAdmin):
    list_display = ('user', 'account', 'card', 'category_display', 'description', 'value', 'installments', 'occurred_at')
    list_filter = ('account', 'occurred_at')
    search_fields = ('description', 'category__description', 'account__description', 'user__username')
    autocomplete_fields = ('user', 'account', 'card', 'category')
    list_select_related = ('account', 'card', 'category', 'user')
    date_hierarchy = 'occurred_at'
    ordering = ('-occurred_at', '-id')
    readonly_fields = ('created_at', 'updated_at')
    inlines = (ParcelInline,)

    @admin.display(description='Categoria', ordering='category__description')
    def category_display(self, obj):
        return obj.category_display
