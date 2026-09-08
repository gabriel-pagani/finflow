from django.contrib import admin
from reversion.admin import VersionAdmin

from app.models import Category


@admin.register(Category)
class CategoryAdmin(VersionAdmin):
    list_display = ('description',)
    search_fields = ('description',)
