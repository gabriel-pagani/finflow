from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic.edit import UpdateView

from ..forms.profile import ProfileForm


class ProfileView(LoginRequiredMixin, UpdateView):
    form_class = ProfileForm
    template_name = 'app/profile.html'
    success_url = reverse_lazy('app:profile')

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        # Só a observação é editável; preserve os demais dados do cadastro.
        self.object = form.save(commit=False)
        self.object.save(update_fields=['observations'])
        messages.success(self.request, 'Observação salva.')
        return redirect(self.get_success_url())
