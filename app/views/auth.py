from django.contrib.auth import views as auth_views


class LoginView(auth_views.LoginView):
    template_name = 'app/login.html'
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    next_page = 'app:login'
