from django.contrib import admin
from reversion.admin import VersionAdmin
import reversion
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group as BaseGroup
from app.models import Group


reversion.register(BaseGroup)
reversion.register(Group)
admin.site.unregister(BaseGroup)
@admin.register(Group)
class GroupAdmin(VersionAdmin, BaseGroupAdmin):
    ...
