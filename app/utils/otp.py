"""
O segundo fator, que é um TOTP: o código de seis dígitos do aplicativo
autenticador. O admin já exigia isso pelo OTPAdminSite; aqui é a mesma peça,
para todo mundo, com o cadastro feito pela própria pessoa no primeiro login.
"""
from base64 import b32encode

import qrcode
import qrcode.image.svg
from django.utils.safestring import mark_safe
from django_otp import devices_for_user
from django_otp.plugins.otp_totp.models import TOTPDevice


# O mesmo nome que o scripts/create_totp.py usa, para o dispositivo criado pela
# tela e o criado pelo terminal aparecerem iguais no admin.
DEVICE_NAME = 'default'


def confirmed_device(user):
    """O dispositivo que já passou pela confirmação; None se ainda não há um."""
    return next(devices_for_user(user, confirmed=True), None)


# Enquanto não confirma, o dispositivo fica guardado sem valer para o login:
# quem recarrega a tela no meio do cadastro continua com o segredo que já
# apontou a câmera, em vez de receber outro.
def pending_device(user):
    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(user=user, name=DEVICE_NAME, confirmed=False)
    return device


# A chave para quem não consegue apontar a câmera e vai digitar à mão. O
# aplicativo espera base32, e o que o banco guarda é hexadecimal.
def secret_of(device):
    return b32encode(device.bin_key).decode('ascii')


# O QR Code sai como SVG desenhado aqui, e não como imagem de fora: o endereço
# do otpauth carrega o segredo, e ele não passa por serviço nenhum.
def qr_of(device):
    image = qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    # O desenho é só o caminho dos quadrados; nada do endereço vira texto no SVG.
    return mark_safe(image.to_string(encoding='unicode'))
