from django.contrib import admin
from reversion.admin import VersionAdmin

from app.models import AccessRequest


@admin.register(AccessRequest)
class AccessRequestAdmin(VersionAdmin):
    list_display = ('ip', 'user', 'created_at',)
    list_filter = ('created_at',)
    search_fields = ('ip', 'user__username', 'user__email',)
    list_select_related = ('user',)
    readonly_fields = ('ip', 'user', 'created_at',)

    def has_add_permission(self, request):
        return False
