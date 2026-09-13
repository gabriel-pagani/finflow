import reversion
from django.contrib import admin
from reversion.admin import VersionAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from app.models import AccessRequest, User


@admin.register(User)
class UserAdmin(VersionAdmin, BaseUserAdmin):
    list_display = ('username', 'first_name', 'last_name', 'email', 'last_login', 'is_staff', 'is_superuser', 'is_active',)
    search_fields = ('username', 'email', 'first_name', 'last_name', 'observations',)
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'groups',)
    actions = ('release_access',)
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

    @admin.action(description='Liberar acesso')
    def release_access(self, request, queryset):
        pending = list(queryset.filter(is_active=False, access_requests__isnull=False).distinct())

        with reversion.create_revision():
            reversion.set_user(request.user)
            reversion.set_comment('Acesso liberado pela ação do portal.')
            for user in pending:
                user.is_active = True
                user.save(update_fields=['is_active'])

        AccessRequest.objects.filter(user__in=pending).delete()

        released = len(pending)
        label = 'solicitação de acesso aprovada' if released == 1 else 'solicitações de acesso aprovadas'
        message = f'{released} {label}.'

        self.message_user(request, message)
