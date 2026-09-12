from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction as db
from django.db.models.deletion import ProtectedError, RestrictedError
from django.forms.models import model_to_dict
from django.utils import timezone

from app.forms import CardForm, InstallmentForm, TransactionForm, TransferForm
from app.models import Card, Installment, Method, Nature, Transaction, Transfer
from app.utils.dates import add_months
from app.utils.formatting import format_to_money

from .models import Action, Kind, Message, Proposal, Role, Status


@dataclass(frozen=True)
class Spec:
    model: type
    form: type
    actions: tuple


# Parcelamento e transferência não se editam, como na tela: alterá-los exigiria
# regerar as transações filhas.
SPECS = {
    Kind.CARD: Spec(Card, CardForm, (Action.CREATE, Action.UPDATE, Action.DELETE)),
    Kind.TRANSACTION: Spec(Transaction, TransactionForm, (Action.CREATE, Action.UPDATE, Action.DELETE)),
    Kind.INSTALLMENT: Spec(Installment, InstallmentForm, (Action.CREATE, Action.DELETE)),
    Kind.TRANSFER: Spec(Transfer, TransferForm, (Action.CREATE, Action.DELETE)),
}

EMPTY = '—'


class ProposalError(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class Rollback(Exception):
    pass


def to_data(value):
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def snapshot(instance):
    return {field.attname: to_data(getattr(instance, field.attname)) for field in instance._meta.concrete_fields}


def display(model, name, value):
    field = model._meta.get_field(name)

    if name == 'category' and value is None:
        return 'Categoria Não Identificada'
    if value in (None, ''):
        return EMPTY
    if field.choices:
        return str(dict(field.flatchoices).get(value, value))
    if isinstance(value, Decimal):
        return format_to_money(value)
    if isinstance(value, date):
        return value.strftime('%d/%m/%Y')
    return str(value)


def row(label, value, before=None):
    entry = {'label': label, 'value': value}
    if before is not None and before != value:
        entry['before'] = before
    return entry


def form_rows(spec, form, before=None):
    rows = []
    for name, field in form.fields.items():
        value = display(spec.model, name, form.cleaned_data.get(name))
        previous = display(spec.model, name, before[name]) if before is not None else None
        if value == EMPTY and previous in (None, EMPTY):
            continue
        rows.append(row(str(field.label), value, previous))
    return rows


def instance_rows(spec, instance):
    rows = []
    for name, field in spec.form.base_fields.items():
        value = display(spec.model, name, getattr(instance, name))
        if value != EMPTY:
            rows.append(row(str(field.label), value))
    return rows


def charge_rows(form, before):
    transaction = form.instance
    credit = transaction.method == Method.CREDIT
    previous = before and before['method'] == Method.CREDIT
    if not credit and not previous:
        return [], []

    value = transaction.calculate_effective_at().strftime('%d/%m/%Y')
    old = before['effective_at'].strftime('%d/%m/%Y') if before else None
    notes = ['No crédito, a transação entra na data de vencimento da fatura em que a compra caiu.'] if credit else []
    return [row('Data Efetiva', value, old)], notes


def parcel_rows(form):
    installment = form.instance
    values = installment.parcel_values()
    count = len(values)

    if values[0] == values[-1]:
        split = f'{count}x de {format_to_money(values[0])}'
    else:
        split = f'{count - 1}x de {format_to_money(values[0])} + 1x de {format_to_money(values[-1])}'

    first = installment.card.charge_date(installment.occurred_at)
    last = installment.card.charge_date(add_months(installment.occurred_at, count - 1))

    return [
        row('Parcelas', split),
        row('1ª Parcela em', first.strftime('%d/%m/%Y')),
        row('Última Parcela em', last.strftime('%d/%m/%Y')),
    ]


def check_delete(instance):
    # Apaga de verdade e desfaz: a regra que vale é a do delete() e do banco,
    # e não uma cópia dela escrita aqui.
    try:
        with db.atomic():
            type(instance).objects.get(pk=instance.pk).delete()
            raise Rollback
    except Rollback:
        pass
    except ValidationError as error:
        raise ProposalError(' '.join(error.messages))
    except (ProtectedError, RestrictedError):
        raise ProposalError('O registro está em uso por outros lançamentos e não pode ser apagado.')


def read_target(spec, kind, user, arguments):
    pk = arguments.get('id')
    if not isinstance(pk, int) or isinstance(pk, bool):
        raise ProposalError('Editar e apagar exigem o "id" numérico do registro.')

    instance = spec.model.objects.filter(user=user, pk=pk).first()
    if instance is None:
        raise ProposalError(f'Não existe {Kind(kind).label.lower()} com id {pk} para este usuário.')

    if kind == Kind.TRANSACTION and instance.is_derived:
        if instance.installment_id:
            raise ProposalError(f'A transação {pk} é parcela do parcelamento {instance.installment_id}: parcela não se edita, e apagá-la é apagar o parcelamento inteiro, com propor_parcelamento.')
        raise ProposalError(f'A transação {pk} é perna da transferência {instance.transfer_id}: ela não se edita, e apagá-la é apagar a transferência inteira, com propor_transferencia.')

    return instance


def build(kind, user, arguments):
    spec = SPECS[kind]
    action = arguments.get('action')
    if action not in spec.actions:
        raise ProposalError(f'Ação inválida para {Kind(kind).label.lower()}: {action!r}. Aceitas: {", ".join(spec.actions)}.')

    fields = spec.form.base_fields
    provided = {name: value for name, value in arguments.items() if name not in ('action', 'id')}
    unknown = sorted(set(provided) - set(fields))
    if unknown:
        raise ProposalError(f'Campos que não existem aqui: {", ".join(unknown)}. Aceitos: {", ".join(fields)}.')

    instance = read_target(spec, kind, user, arguments) if action != Action.CREATE else None
    title = f'{Action(action).label} {Kind(kind).label.lower()}'

    if action == Action.DELETE:
        if provided:
            raise ProposalError('Para apagar, informe só o id.')
        check_delete(instance)
        return instance.pk, {}, snapshot(instance), delete_summary(spec, kind, instance, title)

    if instance:
        # Antes do form: o is_valid() do ModelForm escreve na própria instância.
        state = snapshot(instance)
        before = {name: getattr(instance, name) for name in (*fields, 'effective_at') if hasattr(instance, name)}
        data = {name: to_data(value) for name, value in model_to_dict(instance, fields=list(fields)).items() if value is not None}
    else:
        # Na criação, parte do que a tela já abre preenchido: a data de hoje e
        # os padrões do model, como a natureza normal.
        state, before = {}, None
        blank = spec.form(user=user)
        data = {name: to_data(blank[name].initial) for name in fields if blank[name].initial not in (None, '')}

    for name, value in provided.items():
        if value in (None, ''):
            data.pop(name, None)
        else:
            data[name] = value

    form = spec.form(data=data, instance=instance, user=user)
    if not form.is_valid():
        errors = {name: list(messages) for name, messages in form.errors.items()}
        raise ProposalError('Os dados violam as regras do sistema e nada foi proposto ao usuário.', errors)

    rows = form_rows(spec, form, before)
    if action == Action.UPDATE and not any('before' in entry for entry in rows):
        raise ProposalError('Nada muda em relação ao registro atual: não há o que propor.')

    notes = []
    if kind == Kind.TRANSACTION:
        extra, notes = charge_rows(form, before)
        rows += extra
    elif kind == Kind.INSTALLMENT:
        rows += parcel_rows(form)
        notes.append('Gera uma transação por parcela, cada uma no vencimento da fatura do seu mês.')
    elif kind == Kind.TRANSFER:
        notes.append(f'Gera uma saída em {Method.DEBIT.label} na conta de origem e uma entrada na de destino, as duas como {Nature.INTERNAL.label}.')
    elif kind == Kind.CARD and action == Action.UPDATE and any('before' in entry for entry in rows):
        notes.append('Um ciclo novo vale só para as próximas compras; os lançamentos já feitos mantêm a data que tinham.')

    summary = {'title': title, 'action': action, 'rows': rows, 'notes': notes}
    return instance.pk if instance else None, data, state, summary


def delete_summary(spec, kind, instance, title):
    notes = []
    if kind == Kind.INSTALLMENT:
        notes.append(f'Apaga junto as {instance.transactions.count()} parcelas geradas por ele.')
    elif kind == Kind.TRANSFER:
        notes.append('Apaga junto as duas transações geradas pela transferência.')
    notes.append('Esta ação não pode ser desfeita.')
    return {'title': title, 'action': Action.DELETE, 'rows': instance_rows(spec, instance), 'notes': notes}


def propose(kind, user, conversation, arguments):
    try:
        target_id, payload, state, summary = build(kind, user, arguments)
    except ProposalError as error:
        response = {'ok': False, 'error': str(error)}
        if error.errors:
            response['errors'] = error.errors
        return response, None

    proposal = Proposal.objects.create(
        user=user,
        conversation=conversation,
        kind=kind,
        action=arguments['action'],
        target_id=target_id,
        payload=payload,
        snapshot=state,
        summary=summary,
    )

    return {
        'ok': True,
        'status': 'aguardando_confirmacao',
        'proposal_id': proposal.pk,
        'summary': summary,
        'message': (
            'NADA foi gravado. O usuário está vendo este resumo num card com os botões Confirmar e Descartar, '
            'e só o clique dele grava. Escreva uma frase curta pedindo a confirmação no card, sem repetir os '
            'dados e sem dizer que foi feito.'
        ),
    }, proposal


def apply(proposal):
    spec = SPECS[proposal.kind]

    instance = None
    if proposal.action != Action.CREATE:
        instance = spec.model.objects.select_for_update().filter(user=proposal.user, pk=proposal.target_id).first()
        if instance is None:
            raise ProposalError('O registro não existe mais.')
        if snapshot(instance) != proposal.snapshot:
            raise ProposalError('O registro mudou desde a proposta. Peça de novo ao assistente.')

    if proposal.action == Action.DELETE:
        label = str(instance)
        try:
            instance.delete()
        except ValidationError as error:
            raise ProposalError(' '.join(error.messages))
        except (ProtectedError, RestrictedError):
            raise ProposalError('O registro está em uso por outros lançamentos e não pode ser apagado.')
        return label

    form = spec.form(data=proposal.payload, instance=instance, user=proposal.user)
    if not form.is_valid():
        raise ProposalError(next(iter(message for messages in form.errors.values() for message in messages), 'Os dados não são mais válidos.'))
    return str(form.save())


def resolve(proposal, status, result):
    proposal.status = status
    proposal.result = result[:300]
    proposal.resolved_at = timezone.now()
    proposal.save(update_fields=['status', 'result', 'resolved_at'])

    # O modelo precisa saber o desfecho, senão segue oferecendo gravar o que já
    # foi gravado. Não aparece no chat: quem clicou acabou de ver o card mudar.
    outcome = {
        Status.CONFIRMED: f'O usuário confirmou a proposta {proposal.pk} ({proposal.summary["title"]}) e ela FOI gravada: {result}.',
        Status.CANCELLED: f'O usuário descartou a proposta {proposal.pk} ({proposal.summary["title"]}). Nada foi gravado.',
        Status.FAILED: f'O usuário tentou confirmar a proposta {proposal.pk} ({proposal.summary["title"]}), mas ela NÃO foi gravada: {result}',
    }[status]
    Message.objects.create(conversation=proposal.conversation, role=Role.USER, content=outcome, items=[{'role': 'user', 'content': f'[{outcome}]'}], visible=False)


def confirm(proposal):
    if not proposal.is_open:
        raise ProposalError({
            Status.CONFIRMED: 'Esta proposta já foi confirmada.',
            Status.CANCELLED: 'Esta proposta foi descartada.',
            Status.FAILED: 'Esta proposta já foi recusada.',
        }.get(proposal.status, 'Esta proposta expirou. Peça de novo ao assistente.'))

    try:
        with db.atomic():
            label = apply(proposal)
    except ProposalError as error:
        resolve(proposal, Status.FAILED, str(error))
        raise

    resolve(proposal, Status.CONFIRMED, label)
    return label


def cancel(proposal):
    if proposal.status != Status.PENDING:
        raise ProposalError('Esta proposta não está mais aberta.')
    resolve(proposal, Status.CANCELLED, '')
