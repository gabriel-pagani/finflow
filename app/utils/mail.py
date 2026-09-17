"""
Os avisos que o sistema manda por e-mail.

Avisar é secundário ao que a pessoa pediu: quando isto roda, o pedido de conta
já está gravado, e servidor de e-mail fora do ar não pode derrubar a tela nem
desfazer o cadastro. Por isso toda falha daqui vira log, e não exceção.
"""
import logging

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.http import urlencode


logger = logging.getLogger(__name__)

EMPTY = '—'


# Só os ativos: quem está desligado não entra no portal para liberar nada, e o
# aviso seria um e-mail que ninguém pode atender.
def superuser_emails():
    return list(
        get_user_model().objects
        .filter(is_superuser=True, is_active=True)
        .exclude(email__isnull=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )


def access_request_body(request, applicant):
    # A busca já deixa o pedinte sozinho na lista, com a ação Liberar acesso à
    # mão: sem ela, o administrador procura o nome no meio de todo mundo.
    panel = request.build_absolute_uri(
        f"{reverse('admin:app_user_changelist')}?{urlencode({'q': applicant.username})}"
    )

    return '\n'.join([
        f'{applicant.get_full_name() or applicant.username} solicitou uma conta para acessar o sistema.',
        '',
        f'Nome: {applicant.get_full_name() or EMPTY}',
        f'Usuário: {applicant.username}',
        f'E-mail: {applicant.email or EMPTY}',
        '',
        'Acesso rápido:',
        panel,
    ])


def notify_access_request(request, applicant):
    """Avisa os superusuários do pedido novo. Devolve quantas mensagens saíram."""
    recipients = superuser_emails()
    if not recipients:
        logger.warning('Pedido de acesso de %r sem aviso: nenhum superusuário tem e-mail cadastrado.', applicant.username)
        return 0

    try:
        return send_mail(
            'Nova solicitação de acesso ao FinFlow',
            access_request_body(request, applicant),
            # None é o DEFAULT_FROM_EMAIL.
            None,
            recipients,
        )
    # Qualquer coisa: endereço torto, DNS, TLS, servidor mudo. O pedido está
    # gravado, e nada que aconteça aqui é assunto de quem preencheu a tela.
    except Exception:
        logger.exception('Aviso do pedido de acesso de %r não pôde ser enviado.', applicant.username)
        return 0
