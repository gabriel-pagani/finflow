from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from django.utils import timezone
from .models import Account, Card, Category, Installment, Method, Nature, Transaction, Transfer


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


class CardForm(OwnedForm):
    """Cadastro dos cartões do próprio usuário.

    Fechamento e vencimento são dia do mês, não data: o ciclo se repete, e é
    dele que sai a data de cada compra no crédito. O dono vem do OwnedForm,
    como nos demais registros com usuário.
    """

    class Meta:
        model = Card
        fields = ('account', 'last_digits', 'closing_day', 'due_day',)
        error_messages = {
            NON_FIELD_ERRORS: {
                'unique_together': 'Você já tem um cartão com este final nesta conta.',
            },
        }
        widgets = {
            # inputmode numérico no celular, mas o campo continua texto: os
            # dígitos são identificação, e um number comeria o zero à esquerda.
            'last_digits': forms.TextInput(attrs={'inputmode': 'numeric', 'maxlength': '4', 'pattern': r'\d{4}'}),
            'closing_day': forms.NumberInput(attrs={'min': '1', 'max': '31', 'step': '1'}),
            'due_day': forms.NumberInput(attrs={'min': '1', 'max': '31', 'step': '1'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['account'].queryset = Account.objects.all()
        # self.fields['closing_day'].help_text = 'Dia do mês em que a fatura fecha.'
        # self.fields['due_day'].help_text = 'Dia do mês em que a fatura vence.'

    def validate_unique(self):
        """Checa a unicidade contando o dono, que não é campo do formulário.

        O Django tira da checagem os campos que o formulário não expõe, e com
        isso a regra (usuário, conta, final) sairia inteira — o choque só
        apareceria como IntegrityError na hora de gravar. O dono já está na
        instância neste ponto: quem o amarra é o _post_clean do OwnedForm.
        """
        exclude = {name for name in self._get_validation_exclusions() if name != 'user'}
        try:
            self.instance.validate_unique(exclude=exclude)
        except forms.ValidationError as error:
            self._update_errors(error)


class CardChoiceMixin:
    """Campo de cartão restrito aos cartões do próprio usuário.

    Não há opção vazia: o cartão só é oferecido onde é obrigatório — no
    crédito — e uma escolha "sem cartão" só serviria para burlar a regra que
    calcula a data da compra. Fora do crédito o campo nem chega a ser exibido.
    """

    # Frase para quem ainda não cadastrou cartão nenhum: precisa saber onde
    # fazer isso, não só que faltou preencher um campo.
    NO_CARDS_ERROR = 'Você ainda não tem cartões cadastrados. Cadastre um em Cartões para lançar no crédito.'
    REQUIRED_ERROR = 'Escolha o cartão usado na compra.'

    def setup_card_field(self, help_text):
        field = self.fields['card']
        field.queryset = Card.objects.filter(user=self.user).select_related('account')
        # field.help_text = help_text
        # empty_label=None tira o '---------' do select: com o campo visível
        # apenas no crédito, a lista só precisa dos cartões de verdade.
        field.empty_label = None
        # A obrigatoriedade é decidida no clean(), que conhece o método
        # escolhido; aqui o campo fica opcional para o POST fora do crédito,
        # que nem envia o campo, não ser recusado.
        field.required = False

    def validate_card(self, cleaned, required):
        """Aplica a obrigatoriedade do cartão e devolve o valor final.

        Fora do crédito o cartão é descartado em vez de recusado: o campo nem
        aparece na tela, e um valor que sobrou de uma troca de método é ruído,
        não erro de quem preencheu.
        """
        if not required:
            return None

        # Campo que já falhou por conta própria — uma pk que não é do dono, por
        # exemplo — não ganha um segundo erro dizendo que ficou vazio.
        if 'card' in self.errors:
            return None

        card = cleaned.get('card')
        if not card:
            raise forms.ValidationError({'card': self.REQUIRED_ERROR if self.fields['card'].queryset.exists() else self.NO_CARDS_ERROR})
        return card


class TransactionForm(CardChoiceMixin, OwnedForm):
    """Transações avulsas do próprio usuário: as de origem (parcelamento,
    transferência, investimento) nascem do formulário do respectivo registro."""

    class Meta:
        model = Transaction
        fields = ('datetime', 'account', 'type', 'method', 'card', 'nature', 'category', 'description', 'value',)
        widgets = {
            'datetime': DateTimeLocalInput(),
            'value': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'description': forms.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['account'].queryset = Account.objects.all()

        # O campo só aparece quando o método é Crédito: é o JS da listagem que
        # o mostra, e que o desabilita fora do crédito para ele não ser enviado
        # escondido. A data digitada é a da compra, e o que fica gravado é o
        # vencimento — dizer isso na tela evita o susto de salvar um lançamento
        # e vê-lo aparecer semanas à frente.
        # self.setup_card_field('A data informada é a da compra: o lançamento é gravado no vencimento da fatura correspondente.')

        # Natureza é a exceção do formulário: quem não a informa está lançando
        # movimento comum, então o POST sem o campo cai no default do model em
        # vez de ser recusado por obrigatoriedade.
        self.fields['nature'].required = False

    def clean_nature(self):
        # required=False devolve string vazia, que o campo não aceita; sem esta
        # tradução o lançamento comum quebraria na validação do model.
        return self.cleaned_data.get('nature') or Nature.REGULAR

    def clean(self):
        """Exige o cartão no crédito e troca a data da compra pela do vencimento.

        A troca de data só acontece na criação: depois de salva, o que está na
        tela já é o vencimento, e recalcular a partir dele empurraria a
        transação uma fatura adiante a cada vez que ela fosse salva. Na edição,
        portanto, a data digitada vale como está.
        """
        cleaned = super().clean()

        card = cleaned['card'] = self.validate_card(cleaned, required=cleaned.get('method') == Method.CREDIT)

        moment = cleaned.get('datetime')
        if not self.instance.pk and card and moment:
            cleaned['datetime'] = card.invoice_datetime(moment)

        return cleaned

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


class InstallmentForm(CardChoiceMixin, OwnedForm):
    """Compra parcelada no crédito. O valor informado é o total: quem divide
    entre as parcelas e cria uma transação para cada uma é o próprio model."""

    class Meta:
        model = Installment
        fields = ('datetime', 'account', 'card', 'category', 'description', 'value', 'installments',)
        widgets = {
            'datetime': DateTimeLocalInput(),
            'value': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'installments': forms.NumberInput(attrs={'min': '2', 'step': '1'}),
            'description': forms.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['account'].queryset = Account.objects.all()

        # Parcelamento é sempre crédito, então o cartão é sempre exigido e o
        # campo nunca some da tela — ao contrário da transação avulsa, onde o
        # método é escolhido e o campo aparece só quando cabe.
        self.setup_card_field('Cada parcela cai no vencimento da sua fatura.')

    def clean(self):
        cleaned = super().clean()
        cleaned['card'] = self.validate_card(cleaned, required=True)
        return cleaned


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
