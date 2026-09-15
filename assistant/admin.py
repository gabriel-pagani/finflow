import json

from django.contrib import admin
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from .models import Attachment, Command, Conversation, DailyUsage, Message, Proposal, Role


def pretty(value):
    return format_html('<pre style="white-space: pre-wrap; margin: 0;">{}</pre>', json.dumps(value, ensure_ascii=False, indent=2))


def called_tools(message):
    return [item.get('name') for item in message.items if item.get('type') == 'function_call']


# Tudo aqui é registro do que o assistente fez, aberto para depuração: mudar à
# mão uma mensagem reescreveria o histórico que volta ao modelo na próxima rodada.
class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Conversation)
class ConversationAdmin(ReadOnlyAdmin):
    list_display = ('user', 'message_count', 'created_at', 'updated_at',)
    search_fields = ('user__username', 'user__email',)
    list_select_related = ('user',)
    fields = ('user', 'created_at', 'updated_at', 'messages_link', 'proposals_link',)
    readonly_fields = fields

    @admin.display(description='Mensagens')
    def message_count(self, obj):
        return obj.messages.count()

    @admin.display(description='Mensagens')
    def messages_link(self, obj):
        url = reverse('admin:assistant_message_changelist')
        return format_html('<a href="{}?conversation__user__id__exact={}">Ver as {} mensagens</a>', url, obj.user_id, obj.messages.count())

    @admin.display(description='Propostas')
    def proposals_link(self, obj):
        url = reverse('admin:assistant_proposal_changelist')
        return format_html('<a href="{}?conversation__id__exact={}">Ver as {} propostas</a>', url, obj.pk, obj.proposals.count())


STEP_STYLE = 'display: inline-block; padding: 2px 8px; border-radius: 10px; font-weight: 600; white-space: nowrap; color: #fff; background: {};'

STEPS = {
    'user': ('Usuário', '#417690'),
    'notice': ('Aviso Interno', '#8a6d3b'),
    'assistant': ('Assistente', '#2e7d32'),
    'call': ('Chamada de Ferramenta', '#6a1b9a'),
    'error': ('Ferramenta Recusou', '#c62828'),
}


def expandable(label, content):
    return format_html('<details><summary style="cursor: pointer;">{}</summary>{}</details>', label, content)


def one_line(text):
    return ' '.join(text.split())


# Toda linha tem a mesma altura: fechada, mostra uma linha de resumo cortada
# pela largura; aberta, troca o resumo pelo conteúdo inteiro, sem repetir o começo.
def collapsible(preview, content):
    return format_html(
        '<details class="assistant-timeline"><summary><span class="assistant-timeline-preview">{}</span></summary>'
        '<div class="assistant-timeline-full">{}</div></details>',
        preview, content,
    )


def full_text(text):
    return format_html('<div style="white-space: pre-wrap;">{}</div>', text)


def arguments_of(call):
    try:
        arguments = json.loads(call.get('arguments') or '{}')
    except json.JSONDecodeError:
        return call.get('arguments')
    # No modo estrito todo campo vem preenchido; o null é o que o modelo não usou.
    return {key: value for key, value in arguments.items() if value is not None} if isinstance(arguments, dict) else arguments


def tool_payload(message):
    try:
        payload = json.loads(message.content)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def sent_prompt(message):
    content = message.items[0].get('content') if message.items else None
    if isinstance(content, list):
        return ' '.join(part.get('text', '') for part in content if part.get('type') == 'input_text')
    return content or ''


def result_summary(payload):
    parts = []
    if 'total' in payload:
        total = payload['total']
        parts.append(f'entradas {total["income"]}, saídas {total["outcome"]}, saldo {total["net"]}, {total["count"]} transações')
    if 'groups' in payload:
        parts.append(f'{len(payload["groups"])} grupos por {", ".join(payload["group_by"])}')
    if 'transactions' in payload:
        parts.append(f'{len(payload["transactions"])} de {payload["count"]} transações')
    if 'categories' in payload:
        parts.append(f'{len(payload["accounts"])} contas, {len(payload["categories"])} categorias, {len(payload["cards"])} cartões')
    return '; '.join(parts) or 'retorno sem resumo'


# Uma linha por etapa da conversa: a fala do usuário, cada rodada de ferramentas
# com o retorno de cada chamada logo abaixo dela, e a resposta. O que é longo
# fica recolhido para abrir só quando interessa.
@admin.register(Message)
class MessageAdmin(ReadOnlyAdmin):
    list_display = ('moment', 'user', 'step', 'detail',)
    list_display_links = ('moment',)
    list_filter = ('conversation__user',)
    list_select_related = ('conversation__user', 'attachment',)
    list_per_page = 100
    ordering = ('-created_at', '-id',)
    fields = ('conversation', 'role', 'visible', 'created_at', 'detail', 'formatted_items',)
    readonly_fields = fields

    class Media:
        css = {'all': ('assistant/css/admin.css',)}

    # O retorno já aparece junto da chamada que o pediu; em linha própria, com
    # várias chamadas na mesma rodada, não dá para saber de qual ele é.
    def get_queryset(self, request):
        return super().get_queryset(request).exclude(role=Role.TOOL)

    @admin.display(description='Quando', ordering='created_at')
    def moment(self, obj):
        return timezone.localtime(obj.created_at).strftime('%d/%m/%Y %H:%M:%S')

    @admin.display(description='Usuário', ordering='conversation__user__username')
    def user(self, obj):
        return obj.conversation.user

    def results_of(self, obj):
        calls = called_tools(obj)
        if not calls:
            return {}
        # Os retornos são gravados logo depois da rodada, um por chamada.
        following = Message.objects.filter(conversation_id=obj.conversation_id, role=Role.TOOL, id__gt=obj.id).order_by('id')[:len(calls)]
        return {message.items[0].get('call_id'): tool_payload(message) for message in following if message.items}

    def kind_of(self, obj, results):
        if obj.role == Role.USER:
            return 'user' if obj.visible else 'notice'
        if any(payload.get('ok') is False for payload in results.values()):
            return 'error'
        return 'call' if called_tools(obj) and not obj.content else 'assistant'

    @admin.display(description='Etapa')
    def step(self, obj):
        label, color = STEPS[self.kind_of(obj, self.results_of(obj))]
        return format_html('<span style="{}">{}</span>', STEP_STYLE.format(color), label)

    @admin.display(description='O que Aconteceu')
    def detail(self, obj):
        if obj.role == Role.USER:
            return self.user_detail(obj) if obj.visible else collapsible(one_line(obj.content), full_text(obj.content))

        results = self.results_of(obj)
        calls = [item for item in obj.items if item.get('type') == 'function_call']
        previews = [one_line(obj.content)] if obj.content else []
        blocks = [full_text(obj.content)] if obj.content else []
        for call in calls:
            payload = results.get(call.get('call_id'))
            previews.append(f'{call.get("name")} → {self.outcome_text(payload)}')
            blocks.append(self.call_block(call, payload))
        return collapsible(' · '.join(previews), format_html_join('', '{}', ((block,) for block in blocks)))

    def user_detail(self, obj):
        text = obj.content or '(sem texto)'
        preview, blocks = one_line(text), [full_text(text)]

        attachment = getattr(obj, 'attachment', None)
        if attachment is not None:
            preview = f'{preview} · Anexo: {attachment.get_kind_display()}'
            blocks.append(format_html('<div><em>Anexo: {} ({})</em></div>', attachment.get_kind_display(), attachment.mime))

        # O /comando mostra no chat o que foi digitado, mas o modelo recebe as instruções.
        prompt = sent_prompt(obj)
        if prompt and prompt != obj.content:
            blocks.append(expandable('Texto enviado ao modelo', format_html('<pre style="white-space: pre-wrap;">{}</pre>', prompt)))
        return collapsible(preview, format_html_join('', '{}', ((block,) for block in blocks)))

    def proposal_state(self, payload):
        proposal = Proposal.objects.filter(pk=payload['proposal_id']).first()
        return proposal.get_status_display() if proposal else 'apagada'

    def outcome_text(self, payload):
        if payload is None:
            return 'sem retorno gravado'
        if payload.get('ok') is False:
            return f'recusou: {payload.get("error")}'
        if 'proposal_id' in payload:
            return f'Proposta #{payload["proposal_id"]}: {payload["summary"]["title"]} ({self.proposal_state(payload)})'
        return result_summary(payload)

    def call_block(self, call, payload):
        failed = payload is not None and payload.get('ok') is False
        color = STEPS['error' if failed else 'call'][1]

        if payload is not None and not failed and 'proposal_id' in payload:
            url = reverse('admin:assistant_proposal_change', args=[payload['proposal_id']])
            outcome = format_html('<a href="{}">{}</a>', url, self.outcome_text(payload))
        elif failed:
            outcome = format_html('<strong style="color: {};">{}</strong>', color, self.outcome_text(payload))
        else:
            outcome = self.outcome_text(payload)

        return format_html(
            '<div style="border-left: 3px solid {}; padding-left: 8px; margin: 4px 0 8px;">'
            '<div><code>{}</code> {}</div><div>→ {}</div>{}</div>',
            color, call.get('name'), json.dumps(arguments_of(call), ensure_ascii=False), outcome,
            expandable('Retorno completo', pretty(payload)) if payload is not None else '',
        )

    @admin.display(description='Itens')
    def formatted_items(self, obj):
        return pretty(obj.items)


@admin.register(Proposal)
class ProposalAdmin(ReadOnlyAdmin):
    list_display = ('id', 'user', 'action', 'kind', 'target_id', 'status', 'result', 'created_at', 'resolved_at',)
    list_filter = ('status', 'kind', 'action', 'created_at', 'user',)
    search_fields = ('user__username', 'result',)
    list_select_related = ('user',)
    fields = ('user', 'conversation', 'kind', 'action', 'target_id', 'status', 'result', 'created_at', 'resolved_at',
              'formatted_summary', 'formatted_payload', 'formatted_snapshot',)
    readonly_fields = fields

    @admin.display(description='Resumo Exibido')
    def formatted_summary(self, obj):
        return pretty(obj.summary)

    @admin.display(description='Dados')
    def formatted_payload(self, obj):
        return pretty(obj.payload)

    @admin.display(description='Registro na Proposta')
    def formatted_snapshot(self, obj):
        return pretty(obj.snapshot)


@admin.register(Attachment)
class AttachmentAdmin(ReadOnlyAdmin):
    list_display = ('id', 'user', 'kind', 'mime', 'message', 'created_at',)
    list_filter = ('kind', 'created_at',)
    search_fields = ('message__conversation__user__username',)
    list_select_related = ('message__conversation__user',)
    fields = ('message', 'kind', 'mime', 'file_name', 'created_at',)
    readonly_fields = fields

    # Só o nome: o arquivo não tem URL pública, sai pela view do chat e só para o dono.
    @admin.display(description='Arquivo')
    def file_name(self, obj):
        return obj.file.name

    @admin.display(description='Usuário', ordering='message__conversation__user__username')
    def user(self, obj):
        return obj.message.conversation.user


@admin.register(Command)
class CommandAdmin(ReadOnlyAdmin):
    list_display = ('name', 'user', 'created_at', 'updated_at',)
    list_filter = ('user',)
    search_fields = ('name', 'instructions', 'user__username',)
    list_select_related = ('user',)
    fields = ('user', 'name', 'instructions', 'created_at', 'updated_at',)
    readonly_fields = fields


# Apagar a linha de hoje devolve o dia inteiro ao usuário.
@admin.register(DailyUsage)
class DailyUsageAdmin(ReadOnlyAdmin):
    list_display = ('day', 'user', 'messages', 'limit',)
    list_filter = ('day', 'user',)
    search_fields = ('user__username',)
    list_select_related = ('user',)
    fields = ('user', 'day', 'messages', 'limit',)
    readonly_fields = fields

    # Pelas permissões de agora: num dia passado o teto pode ter sido outro.
    @admin.display(description='Limite Atual')
    def limit(self, obj):
        limit = DailyUsage.limit_for(obj.user)
        return 'Ilimitado' if limit is None else limit
