from django import forms

from ..models import User


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
