"""Entrada e saída da sessão."""

from django.contrib.auth import views as auth_views


class LoginView(auth_views.LoginView):
    """Tela de login do sistema. O portal de administração segue com o login próprio."""

    template_name = 'app/login.html'
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    """Encerra a sessão e devolve o usuário para a tela de login."""

    next_page = 'app:login'
