"""
Os tetos de tentativa que moram no cache.

Contam por chave e por janela, e guardam só o número: a janela vence sozinha, e
ninguém precisa limpar nada depois.
"""
from django.core.cache import cache


# O teto de palpites de senha do sistema inteiro, contados de qualquer rede e
# para qualquer usuário.
#
# O axes tranca o par usuário e IP: quem tem uma lista de endereços, ou de
# usuários, recomeça o contador em cada par e nunca chega ao teto dele. Este
# conta todo palpite errado numa chave só, e por isso vê o ataque distribuído
# como uma coisa só, mude o atacante o que mudar.
#
# O sistema tem um usuário só, e o preço é dele: quem estoura o teto, de onde
# for, tranca a entrada de todo mundo por uma hora. O acerto zera a conta.
LOGIN_FAILURES = 'login:failures'
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


def login_blocked():
    """Diz se o sistema já recebeu palpites errados demais, somando todas as fontes."""
    return cache.get(LOGIN_FAILURES, 0) >= LOGIN_FAILURES_LIMIT


def login_failure():
    count(LOGIN_FAILURES, LOGIN_FAILURES_WINDOW)


def login_success():
    cache.delete(LOGIN_FAILURES)
