from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.functional import cached_property
from django.views.generic.edit import CreateView, FormView
from django_otp import login as otp_login

from ..forms import AccessRequestForm, LoginForm, OtpSetupForm
from ..utils.otp import confirmed_device, pending_device, qr_of, secret_of
from ..utils.request import get_client_ip


class LoginView(auth_views.LoginView):
    template_name = 'app/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

    # A sessão só conta como verificada quando o código confere. Sem isto, quem
    # acabou de entrar com o código certo seria devolvido ao cadastro pelo
    # middleware, como se não tivesse nenhum aplicativo.
    def form_valid(self, form):
        response = super().form_valid(form)
        if form.device is not None:
            otp_login(self.request, form.device)
        return response


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
        messages.success(self.request,
            'Solicitação enviada! A conta só funcionará depois que algum administrador aprovar a solicitação, '
            'e até lá a conta ficará desabilitada.'
        )
        return response
