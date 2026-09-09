from django.contrib import admin
from reversion.admin import VersionAdmin

from app.models import Subscription, SubscriptionPeriod, Transaction


class PeriodInline(admin.TabularInline):
    model = SubscriptionPeriod
    fields = ('started_at', 'cancelled_at')
    ordering = ('started_at',)
    extra = 0
    verbose_name = 'Período'
    verbose_name_plural = 'Períodos'


class ChargeInline(admin.TabularInline):
    model = Transaction
    fields = ('reference', 'value', 'effective_at')
    readonly_fields = fields
    ordering = ('-reference',)
    extra = 0
    can_delete = False
    verbose_name = 'Cobrança'
    verbose_name_plural = 'Cobranças'

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Subscription)
class SubscriptionAdmin(VersionAdmin):
    list_display = ('user', 'account', 'card', 'category_display', 'description', 'value', 'recurrence', 'is_active')
    list_filter = ('recurrence', 'account', 'card')
    search_fields = ('description', 'category__description', 'account__description', 'user__username')
    autocomplete_fields = ('user', 'account', 'card', 'category')
    list_select_related = ('account', 'card', 'category', 'user')
    ordering = ('description',)
    readonly_fields = ('created_at', 'updated_at')
    inlines = (PeriodInline, ChargeInline)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('periods')

    @admin.display(description='Ativa', boolean=True)
    def is_active(self, obj):
        return obj.is_active

    @admin.display(description='Categoria', ordering='category__description')
    def category_display(self, obj):
        return obj.category_display
