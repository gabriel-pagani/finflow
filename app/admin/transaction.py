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
    readonly_fields = ('installment', 'parcel', 'transfer', 'subscription', 'reference', 'effective_at', 'created_at', 'updated_at')

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.is_derived:
            return [field.name for field in self.model._meta.fields if field.name != 'id']
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_derived and not self.is_cascade_from_origin(request):
            return False
        return super().has_delete_permission(request, obj)

    def is_cascade_from_origin(self, request):
        # O admin repete a pergunta para cada objeto que a cascata levaria junto.
        match = getattr(request, 'resolver_match', None)
        prefix = f'{self.opts.app_label}_{self.opts.model_name}_'

        return bool(match and match.url_name and not match.url_name.startswith(prefix))

    @admin.display(description='Categoria', ordering='category__description')
    def category_display(self, obj):
        return obj.category_display

    @admin.display(description='Parcela', ordering='parcel')
    def parcel_display(self, obj):
        return f'{obj.parcel}/{obj.installment.installments}' if obj.installment_id else ''
