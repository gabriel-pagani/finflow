from django.contrib import admin
from reversion.admin import VersionAdmin

from app.models import BankConnection


# O id vem do painel da Pluggy (Demo → Connected Items); a tela Open Finance busca
# as contas e as transações dele sozinha.
@admin.register(BankConnection)
class BankConnectionAdmin(VersionAdmin):
    list_display = ('__str__', 'user', 'since', 'synced_at')
    readonly_fields = ('synced_at',)
