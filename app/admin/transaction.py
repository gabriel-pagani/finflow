from django.contrib import admin
from reversion.admin import VersionAdmin

from app.models import Transaction


@admin.register(Transaction)
class TransactionAdmin(VersionAdmin):
    list_display = ('user', 'account', 'nature', 'type', 'method', 'card', 'category_display', 'parcel_display', 'description', 'value', 'effective_at')
    list_filter = ('type', 'method', 'nature', 'account', 'effective_at')
    search_fields = ('description', 'category__description', 'account__description', 'user__username')
    autocomplete_fields = ('user', 'account', 'card', 'category')
    list_select_related = ('account', 'card', 'category', 'installment', 'user')
    date_hierarchy = 'effective_at'
    ordering = ('-effective_at', '-id')
    readonly_fields = ('installment', 'parcel', 'transfer', 'effective_at', 'created_at', 'updated_at')

    @admin.display(description='Categoria', ordering='category__description')
    def category_display(self, obj):
        return obj.category_display

    @admin.display(description='Parcela', ordering='parcel')
    def parcel_display(self, obj):
        return f'{obj.parcel}/{obj.installment.installments}' if obj.installment_id else ''
