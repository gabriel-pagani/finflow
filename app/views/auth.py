from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, login as auth_login, views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.functional import cached_property
from django.views.generic.edit import CreateView, FormView
from django_otp import login as otp_login

from ..forms import AccessRequestForm, LoginForm, LoginTokenForm, OtpSetupForm
from ..utils.mail import notify_access_request
from ..utils.otp import confirmed_device, pending_device, qr_of, secret_of
from ..utils.request import get_client_ip


# Entre a senha e o código o usuário fica guardado na sessão, e só isso: a
# sessão segue anônima, e nada do sistema abre com essa espera na mão.
PENDING = 'otp_pending'

# Tempo para digitar o código. Vencido, a senha é pedida de novo: a espera não
# pode virar meia sessão esquecida num computador emprestado.
PENDING_TIMEOUT = timedelta(minutes=5)


def hold(request, user, next_url):
    request.session[PENDING] = {
        'user': user.pk,
        # O backend que autenticou, para a segunda etapa entrar pelo mesmo.
        'backend': user.backend,
        'next': next_url,
        'since': timezone.now().isoformat(),
    }


def release(request):
    request.session.pop(PENDING, None)


def pending(request):
    """Quem já passou pela senha e ainda deve o código; None se não há ou venceu."""
    data = request.session.get(PENDING)
    if not data:
        return None

    if timezone.now() - datetime.fromisoformat(data['since']) > PENDING_TIMEOUT:
        release(request)
        return None

    # Relido do banco: entre uma etapa e outra a conta pode ter sido desligada.
    return get_user_model().objects.filter(pk=data['user'], is_active=True).first()


class LoginView(auth_views.LoginView):
    template_name = 'app/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

    # Abrir a tela da senha é recomeçar: espera de código que sobrou de outra
    # tentativa não vale mais.
    def get(self, request, *args, **kwargs):
        release(request)
        return super().get(request, *args, **kwargs)

    # Para quem cadastrou o aplicativo, a senha sozinha não abre a sessão: ela
    # leva à segunda etapa, onde o campo do código faz sentido — e é só lá que
    # ele aparece, para quem tem o que digitar.
    def form_valid(self, form):
        user = form.get_user()
        if confirmed_device(user) is None:
            return super().form_valid(form)

        hold(self.request, user, self.get_redirect_url())
        return redirect('app:login_token')


class LoginTokenView(FormView):
    template_name = 'app/login_token.html'
    form_class = LoginTokenForm

    # Sem a senha conferida antes, esta tela não existe.
    def dispatch(self, request, *args, **kwargs):
        self.user = pending(request)
        if self.user is None:
            return redirect('app:login')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'user': self.user}

    def form_valid(self, form):
        destination = self.request.session[PENDING]['next']
        backend = self.request.session[PENDING]['backend']
        release(self.request)

        auth_login(self.request, self.user, backend=backend)
        # A sessão só conta como verificada aqui; sem isto o middleware
        # devolveria a pessoa ao cadastro, como se não tivesse aplicativo.
        otp_login(self.request, form.device)

        return redirect(destination or 'app:overview')


class LogoutView(auth_views.LogoutView):
    next_page = 'app:login'


class OtpSetupView(LoginRequiredMixin, FormView):
    """
    O cadastro do aplicativo autenticador, no primeiro login.

    O segredo nasce aqui e só é confirmado quando a pessoa devolve um código
    gerado por ele: assim ninguém fica com um segundo fator que nunca conseguiu
    usar, trancado do lado de fora da própria conta.
    """

    template_name = 'app/otp_setup.html'
    form_class = OtpSetupForm
    success_url = reverse_lazy('app:overview')
    # Sem o menu da casca: daqui só se sai cadastrando ou saindo.
    extra_context = {'bare': True}

    # Um segundo dispositivo deixaria dois segredos válidos para a mesma conta,
    # e o antigo continuaria entrando. Trocar de celular se faz apagando o
    # dispositivo no portal de administração.
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and confirmed_device(request.user):
            return redirect('app:overview')
        return super().dispatch(request, *args, **kwargs)

    @cached_property
    def device(self):
        return pending_device(self.request.user)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'device': self.device}

    def get_context_data(self, **kwargs):
        return super().get_context_data(qr=qr_of(self.device), secret=secret_of(self.device), **kwargs)

    def form_valid(self, form):
        self.device.confirmed = True
        self.device.save(update_fields=['confirmed'])
        # Entrar no sistema logo depois de cadastrar: a pessoa acabou de provar
        # que tem o aplicativo, e pedir o código de novo seria só atrito.
        otp_login(self.request, self.device)

        messages.success(self.request, 'Verificação em duas etapas ativada. A partir do próximo login, o código será pedido junto da senha.')
        return super().form_valid(form)


class AccessRequestView(CreateView):
    form_class = AccessRequestForm
    template_name = 'app/access_request.html'
    success_url = reverse_lazy('app:login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('app:overview')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['ip'] = get_client_ip(self.request)
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)

        notify_access_request(self.request, self.object)

        messages.success(self.request,
            'Solicitação enviada! A conta só funcionará depois que algum administrador aprovar a solicitação, '
            'e até lá a conta ficará desabilitada.'
        )
        return response
