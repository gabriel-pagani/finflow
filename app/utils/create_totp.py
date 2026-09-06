"""Cria o dispositivo TOTP de um usuário e mostra o QR Code de cadastro.

Executado pelo alvo create-superuser do Makefile, logo após o createsuperuser.
O portal de administração usa OTPAdminSite, que exige segundo fator: um
superusuário sem dispositivo tem senha válida e ainda assim é devolvido para a
tela de login, sem mensagem explicando o motivo.

Sem TOTP_USER definido, age sobre o superusuário criado há menos tempo, que é
o que acabou de sair do createsuperuser. Para um usuário específico, use:
    make create-totp user=<username>
"""

import os
import sys

import qrcode
from django.contrib.auth import get_user_model
from django_otp.plugins.otp_totp.models import TOTPDevice

User = get_user_model()
username = os.getenv('TOTP_USER', '').strip()

if username:
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        sys.exit(f'Usuário "{username}" não encontrado.')
else:
    user = User.objects.filter(is_superuser=True).latest('date_joined')

existing = TOTPDevice.objects.filter(user=user)
if existing.exists():
    nomes = ', '.join(f'"{d.name}"' for d in existing)
    sys.exit(
        f'O usuário "{user.username}" já tem dispositivo TOTP cadastrado ({nomes}).\n'
        f'Nada foi alterado. Para trocar de celular, apague o dispositivo atual antes:\n'
        f"    TOTPDevice.objects.filter(user__username='{user.username}').delete()"
    )

device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)

print()
print(f'Dispositivo TOTP criado para "{user.username}".')
print()
print('Escaneie o QR Code no app autenticador (Google Authenticator, Aegis, 1Password):')
print()

qr = qrcode.QRCode(border=1)
qr.add_data(device.config_url)
qr.make(fit=True)
qr.print_ascii(out=sys.stdout, invert=True)

print()
print('Não conseguiu escanear? Cadastre a chave manualmente:')
print(f'    {device.config_url}')
print()
print('Guarde essa chave em local seguro: sem ela e sem o app, o acesso ao admin se perde.')
