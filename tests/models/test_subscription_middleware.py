"""
O middleware que lança as cobranças no acesso do usuário.

É o único gatilho da geração: quem não abre o sistema não tem cobrança
lançada, e o que ficou vencido sai no acesso seguinte.
"""
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.utils import timezone

from app.middleware import SubscriptionChargesMiddleware
from app.models import Transaction


def build_request(user):
    request = RequestFactory().get('/')
    request.user = user
    request.session = {}
    return request


def primeiro_dia_do_mes():
    return timezone.localdate().replace(day=1)


def test_lanca_no_acesso_do_usuario(subscribe, credit_rule, user):
    subscribe(started_at=primeiro_dia_do_mes())
    SubscriptionChargesMiddleware(lambda request: 'resposta')(build_request(user))
    assert Transaction.objects.count() == 1


def test_roda_uma_vez_por_dia(subscribe, credit_rule, user):
    subscribe(started_at=primeiro_dia_do_mes())
    middleware = SubscriptionChargesMiddleware(lambda request: 'resposta')
    request = build_request(user)
    middleware(request)
    assert request.session[SubscriptionChargesMiddleware.SESSION_KEY] == timezone.localdate().isoformat()
    middleware(request)
    assert Transaction.objects.count() == 1


def test_lanca_so_as_assinaturas_de_quem_acessou(subscribe, credit_rule, user, other_user):
    subscribe(started_at=primeiro_dia_do_mes())
    subscribe(started_at=primeiro_dia_do_mes(), user=other_user, description='Spotify')
    SubscriptionChargesMiddleware(lambda request: 'resposta')(build_request(other_user))
    assert Transaction.objects.count() == 1
    assert Transaction.objects.first().user_id == other_user.pk


def test_ignora_visitante(subscribe, credit_rule):
    subscribe(started_at=primeiro_dia_do_mes())
    SubscriptionChargesMiddleware(lambda request: 'resposta')(build_request(AnonymousUser()))
    assert Transaction.objects.count() == 0


def test_devolve_a_resposta_da_view(user, db):
    resposta = SubscriptionChargesMiddleware(lambda request: 'resposta')(build_request(user))
    assert resposta == 'resposta'
