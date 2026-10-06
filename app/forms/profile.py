from django import forms
from django.core.exceptions import ValidationError

from ..models import User


class AccountForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'username', 'email',)

    # Os mesmos obrigatórios do pedido de acesso, que é onde o cadastro nasce.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].required = True
        self.fields['email'].required = True
        self.fields['username'].help_text = ''

    # O unique do banco diferencia maiúscula; o login e o pedido de acesso não.
    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists():
            raise ValidationError('Já existe um usuário com este nome.')
        return username


class ProfileForm(forms.ModelForm):
    observations = forms.CharField(
        label='Observação', required=False, max_length=2000,
        help_text='Informe preferências para o assistente, como a conta e o cartão que você mais usa. As alterações valem a partir da próxima mensagem.',
        widget=forms.Textarea(attrs={
            'rows': 6,
            'placeholder': 'Uso a conta Nubank por padrão. Para compras no crédito, uso o cartão principal.',
        }),
    )

    class Meta:
        model = User
        fields = ('observations',)
