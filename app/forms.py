from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from django.utils import timezone
from .models import Account, Category, Installment, Nature, Transaction, Transfer


# Mensagem exata levantada por Transaction.clean(); serve de gancho para
# trocá-la por uma que nomeie a combinação recusada.
RULE_ERROR = 'Combinação de conta, tipo e método não permitida pelas regras de negócio.'


class DateTimeLocalInput(forms.DateTimeInput):
    """Campo nativo de data e hora do navegador, no formato que ele espera."""

    input_type = 'datetime-local'

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format='%Y-%m-%dT%H:%M')


class OwnedForm(forms.ModelForm):
    """Base dos formulários cujo registro pertence ao usuário logado.

    Reúne o que Transação, Parcelamento e Transferência fazem igual: amarrar o
    dono à instância antes da validação do model, oferecer a categoria opcional
    com o mesmo rótulo da listagem e abrir o campo de data já no agora.
    """

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        if 'category' in self.fields:
            self.fields['category'].queryset = Category.objects.all()
            self.fields['category'].empty_label = 'Categoria Não Identificada'

        if not self.instance.pk and 'datetime' in self.fields:
            self.fields['datetime'].initial = timezone.localtime().replace(second=0, microsecond=0)

    def clean_value(self):
        value = self.cleaned_data['value']
        if value <= 0:
            raise forms.ValidationError('O valor deve ser maior que zero.')
        return value

    def _post_clean(self):
        # O user tem de estar na instância antes da validação do model: sem ele
        # o full_clean falharia no campo obrigatório em vez de chegar à regra.
        if self.user and not self.instance.user_id:
            self.instance.user = self.user
        super()._post_clean()


class TransactionForm(OwnedForm):
    """Transações avulsas do próprio usuário: as de origem (parcelamento,
    transferência, investimento) nascem do formulário do respectivo registro."""

    class Meta:
        model = Transaction
        fields = ('datetime', 'account', 'type', 'method', 'nature', 'category', 'description', 'value',)
        widgets = {
            'datetime': DateTimeLocalInput(),
            'value': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'description': forms.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['account'].queryset = Account.objects.all()

        # Natureza é a exceção do formulário: quem não a informa está lançando
        # movimento comum, então o POST sem o campo cai no default do model em
        # vez de ser recusado por obrigatoriedade.
        self.fields['nature'].required = False

    def clean_nature(self):
        # required=False devolve string vazia, que o campo não aceita; sem esta
        # tradução o lançamento comum quebraria na validação do model.
        return self.cleaned_data.get('nature') or Nature.REGULAR

    def _post_clean(self):
        # É o Transaction.clean() que aplica a regra de negócio, a mesma fonte
        # usada pelo admin; aqui só se detalha a mensagem, que no model é
        # genérica por não saber qual combinação foi tentada.
        super()._post_clean()

        data = self.cleaned_data
        if data.get('account') and data.get('type') and data.get('method'):
            detailed = (
                f'A conta {data["account"]} não permite '
                f'{dict(self.fields["type"].choices)[data["type"]].lower()} em '
                f'{dict(self.fields["method"].choices)[data["method"]]}.'
            )
            errors = self._errors.get(NON_FIELD_ERRORS)
            if errors and RULE_ERROR in errors:
                self._errors[NON_FIELD_ERRORS] = self.error_class([detailed])


class InstallmentForm(OwnedForm):
    """Compra parcelada no crédito. O valor informado é o total: quem divide
    entre as parcelas e cria uma transação para cada uma é o próprio model."""

    class Meta:
        model = Installment
        fields = ('datetime', 'account', 'category', 'description', 'value', 'installments',)
        widgets = {
            'datetime': DateTimeLocalInput(),
            'value': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'installments': forms.NumberInput(attrs={'min': '2', 'step': '1'}),
            'description': forms.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['account'].queryset = Account.objects.all()
        # A data é a da primeira parcela, não a da compra: as demais saem dela,
        # somando um mês por parcela.
        self.fields['datetime'].label = 'Data e Hora da 1ª Parcela'


class TransferForm(OwnedForm):
    """Movimentação entre contas do próprio usuário. As duas pernas (saída na
    origem, entrada no destino) são geradas pelo model, no save."""

    class Meta:
        model = Transfer
        fields = ('datetime', 'category', 'origin', 'destination', 'description', 'value',)
        widgets = {
            'datetime': DateTimeLocalInput(),
            'value': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'description': forms.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['origin'].queryset = Account.objects.all()
        self.fields['destination'].queryset = Account.objects.all()
