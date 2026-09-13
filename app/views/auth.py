from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView

from ..forms import AccessRequestForm
from ..utils.request import get_client_ip


class LoginView(auth_views.LoginView):
    template_name = 'app/login.html'
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    next_page = 'app:login'


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
