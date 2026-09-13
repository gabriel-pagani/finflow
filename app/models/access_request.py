from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class AccessRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name='access_requests', verbose_name='Usuário')
    ip = models.GenericIPAddressField(verbose_name='Endereço IP')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora da Criação')

    WINDOW = timedelta(days=7)

    @classmethod
    def recent(cls, ip):
        return cls.objects.filter(ip=ip, created_at__gte=timezone.now() - cls.WINDOW).exists()

    def __str__(self):
        return f'{self.ip} ({self.created_at:%d/%m/%Y})'

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['ip', '-created_at'], name='access_request_ip_idx'),
        ]
        verbose_name = 'Pedido de Acesso'
        verbose_name_plural = 'Pedidos de Acesso'
