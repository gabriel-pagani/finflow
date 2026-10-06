from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import TemplateView

from assistant.models import Command, DailyUsage

from ..forms.profile import AccountForm, ProfileForm
from ..models import User


# Cada seção do perfil é um formulário próprio, enviado sozinho: o campo
# "section" diz qual, e as outras voltam como estavam.
SECTIONS = {
    'account': (AccountForm, 'Dados da conta salvos.'),
    'password': (PasswordChangeForm, 'Senha alterada.'),
    'observations': (ProfileForm, 'Observação salva.'),
}


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = 'app/profile.html'

    def build(self, name, data=None):
        form_class = SECTIONS[name][0]
        user = self.request.user
        if form_class is PasswordChangeForm:
            return form_class(user, data)
        # Cópia do usuário: um formulário recusado escreve na instância, e o
        # resto da página (o menu, as outras seções) mostraria o valor recusado.
        return form_class(data, instance=User.objects.get(pk=user.pk))

    def get_context_data(self, **kwargs):
        user = self.request.user
        forms = {name: self.build(name) for name in SECTIONS}
        forms.update(kwargs.pop('bound', {}))
        return super().get_context_data(
            forms=forms,
            assistant={
                'allowed': user.has_perm('assistant.use_assistant'),
                'messages_used': DailyUsage.objects.filter(user=user, day=timezone.localdate()).values_list('messages', flat=True).first() or 0,
                'messages_limit': DailyUsage.limit_for(user),
                'commands_used': Command.objects.filter(user=user).count(),
                'commands_limit': Command.limit_for(user),
            },
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        name = request.POST.get('section')
        if name not in SECTIONS:
            return redirect('app:profile')

        form = self.build(name, request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(bound={name: form}))

        if name == 'password':
            form.save()
            # Sem isto a troca de senha derruba a própria sessão.
            update_session_auth_hash(request, form.user)
        else:
            # Só os campos da seção: o resto do cadastro fica como está.
            form.instance.save(update_fields=list(form.Meta.fields))

        messages.success(request, SECTIONS[name][1])
        return redirect('app:profile')
