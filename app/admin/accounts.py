"""Portal de administração de quem usa o sistema e do cadastro global.

Conta, categoria e regra de negócio só se editam por aqui: são o vocabulário
que as telas oferecem, e mudam com muito menos frequência do que se lança.
"""

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin, UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group as BaseGroup
from reversion.admin import VersionAdmin
import reversion

from ..models import Account, BusinessRule, Category, Group, User


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
