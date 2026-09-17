from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied

from .utils import throttle


class ThrottledModelBackend(ModelBackend):
    """
    A autenticação de sempre, com o teto de palpites por usuário.

    O teto do axes é do par usuário e IP; o deste é só do usuário, e os dois
    contam a mesma tentativa. O tamanho do teto e o porquê dele estão no
    app.utils.throttle.

    Fica no backend, e não na tela de login, porque toda porta que autentica
    passa por aqui — inclusive a do portal de administração, que não passa pelo
    teto do nginx.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.pop(get_user_model().USERNAME_FIELD, None)

        if username and throttle.login_blocked(username):
            # Como o backend do axes: interrompe a fila de backends antes de
            # conferir a senha, e o authenticate devolve None a quem chamou.
            raise PermissionDenied

        user = super().authenticate(request, username=username, password=password, **kwargs)

        if username:
            if user is None:
                throttle.login_failure(username)
            else:
                throttle.login_success(username)

        return user
