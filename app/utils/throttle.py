"""
Os tetos de tentativa que moram no cache.

Contam por chave e por janela, e guardam só o número: a janela vence sozinha, e
ninguém precisa limpar nada depois.
"""
from django.core.cache import cache


# O teto de palpites de senha por usuário, contados de qualquer rede.
#
# O axes tranca o par usuário e IP: quem tem uma lista de endereços recomeça o
# contador em cada um e nunca chega ao teto dele. Este conta só o usuário, e por
# isso vê o ataque distribuído como uma coisa só.
#
# O número é folgado de propósito, e o acerto zera a conta: o teto está aqui
# para o palpite automático, e não para quem esqueceu a senha. Teto baixo por
# usuário daria a qualquer um o poder de trancar a conta de quem ele quisesse,
# de onde quisesse, só errando a senha.
LOGIN_FAILURES = 'login:failures:{}'
LOGIN_FAILURES_LIMIT = 50
LOGIN_FAILURES_WINDOW = 60 * 60


def count(key, window):
    """Soma uma tentativa à chave e devolve o total dela na janela."""
    # O add só vale para a primeira tentativa da janela, e é ele que marca o
    # prazo: o incr soma sem mexer no vencimento.
    cache.add(key, 0, window)
    try:
        return cache.incr(key)
    except ValueError:
        # A chave venceu entre o add e o incr: a janela acabou de recomeçar.
        cache.add(key, 1, window)
        return 1


def login_key(username):
    # Sem olhar a caixa, como o cadastro do Django, que recusa usuário que só
    # difere de maiúscula: senão 'Gabriel' e 'gabriel' teriam contas separadas.
    return LOGIN_FAILURES.format(username.strip().casefold())


def login_blocked(username):
    """Diz se este usuário já errou a senha demais, somando todas as redes."""
    return cache.get(login_key(username), 0) >= LOGIN_FAILURES_LIMIT


def login_failure(username):
    count(login_key(username), LOGIN_FAILURES_WINDOW)


def login_success(username):
    cache.delete(login_key(username))
