from django.contrib import admin
from reversion.admin import VersionAdmin
import reversion
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin, GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group as BaseGroup
from .models import User, Group, Account, Category, BusinessRule, Installment, Investment, Contribution, Redemption, Yield, Transaction


# User Admin
@admin.register(User)
class UserAdmin(VersionAdmin, BaseUserAdmin):
    list_display = ('username', 'first_name', 'last_name', 'email', 'last_login', 'is_staff', 'is_superuser', 'is_active',)
    search_fields = ('username', 'email', 'first_name', 'last_name', 'observations',)
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'groups',)
    filter_horizontal = ('groups', 'user_permissions',)
    model = User
    ordering = ('username',)
    fieldsets = (
        (None, {
            'fields': ('username', 'password',)
        }),
        ('Informações pessoais', {
            'fields': ('first_name', 'last_name', 'email',)
        }),
        ('Permissões', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions',)
        }),
        ('Datas importantes', {
            'fields': ('last_login', 'date_joined',)
        }),
        ('Observações', {
            'fields': ('observations',)
        }),
    )
    add_fieldsets = (
        (None, {
            'fields': ('username', 'password1', 'password2',),
        }),
    )


# Group Admin
reversion.register(BaseGroup)
reversion.register(Group)
admin.site.unregister(BaseGroup)
@admin.register(Group)
class GroupAdmin(VersionAdmin, BaseGroupAdmin):
    ...


@admin.register(Account)
class AccountAdmin(VersionAdmin):
    list_display = ('description',)
    search_fields = ('description',)


@admin.register(Category)
class CategoryAdmin(VersionAdmin):
    list_display = ('description',)
    search_fields = ('description',)


@admin.register(BusinessRule)
class BusinessRuleAdmin(VersionAdmin):
    list_display = ('account', 'type', 'method',)
    list_filter = ('account', 'type', 'method',)
    search_fields = ('account__description',)


@admin.register(Installment)
class InstallmentAdmin(VersionAdmin):
    list_display = ('user', 'account', 'category_display', 'description', 'value', 'installments', 'datetime',)
    list_filter = ('user', 'account', 'category',)
    search_fields = ('description',)
    autocomplete_fields = ('category',)

    @admin.display(description='Categoria', ordering='category__description')
    def category_display(self, obj):
        return obj.category_display

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('user', 'account', 'category', 'description', 'value', 'installments', 'datetime',)
        return ()


class ContributionInline(admin.TabularInline):
    model = Contribution
    extra = 0
    fields = ('value', 'datetime',)


class RedemptionInline(admin.TabularInline):
    model = Redemption
    extra = 0
    fields = ('value', 'datetime',)


class YieldInline(admin.TabularInline):
    model = Yield
    extra = 0
    fields = ('value', 'datetime',)


@admin.register(Investment)
class InvestmentAdmin(VersionAdmin):
    list_display = ('user', 'account', 'description', 'category_display', 'applied_value', 'yielded_value', 'redeemed_value', 'balance',)
    list_filter = ('user', 'account', 'category',)
    search_fields = ('description',)
    autocomplete_fields = ('category',)
    inlines = (ContributionInline, YieldInline, RedemptionInline,)

    @admin.display(description='Categoria', ordering='category__description')
    def category_display(self, obj):
        return obj.category_display

    @admin.display(description='Total Aplicado')
    def applied_value(self, obj):
        return obj.applied_value

    @admin.display(description='Total Rendido')
    def yielded_value(self, obj):
        return obj.yielded_value

    @admin.display(description='Total Resgatado')
    def redeemed_value(self, obj):
        return obj.redeemed_value

    @admin.display(description='Saldo')
    def balance(self, obj):
        return obj.balance

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('user', 'account', 'category', 'description',)
        return ()


@admin.register(Transaction)
class TransactionAdmin(VersionAdmin):
    list_display = ('user', 'account', 'type', 'method', 'category_display', 'description', 'value', 'datetime',)
    list_filter = ('user', 'account', 'type', 'method', 'category',)
    search_fields = ('description',)
    autocomplete_fields = ('category',)
    actions = ('duplicate_transactions',)

    @admin.display(description='Categoria', ordering='category__description')
    def category_display(self, obj):
        return obj.category_display

    @admin.action(description='Duplicar Transações selecionados', permissions=['add'])
    def duplicate_transactions(self, request, queryset):
        with reversion.create_revision():
            reversion.set_user(request.user)
            reversion.set_comment('Duplicado a partir de transação existente.')
            count = 0
            for transaction in queryset:
                Transaction.objects.create(
                    user=transaction.user,
                    account=transaction.account,
                    type=transaction.type,
                    method=transaction.method,
                    category=transaction.category,
                    description=transaction.description,
                    value=transaction.value,
                    datetime=transaction.datetime,
                )
                count += 1

        self.message_user(request, f'{count} transação(ões) duplicada(s) com sucesso.')

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.is_derived:
            return ('user', 'account', 'type', 'method', 'category', 'description', 'value', 'datetime', 'installment', 'parcel', 'investment', 'contribution', 'redemption',)
        return ('installment', 'parcel', 'investment', 'contribution', 'redemption',)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_derived:
            return False
        return super().has_delete_permission(request, obj)
