"""As rotas do chat: o stream da conversa e a confirmação do lançamento.

Todas exigem sessão e a permissão `app.use_assistant` — a mesma que decide se o
botão flutuante chega a ser renderizado. O botão escondido é conveniência; quem
recusa de fato é aqui.

A escrita mora numa rota própria, e não no fim do stream, de propósito. O modelo
propõe, o usuário confirma, e é o POST do usuário — com o CSRF da sessão dele —
que grava. Assim não existe caminho em que algo dito no chat vire dinheiro no
banco sem alguém ter clicado.
"""

import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction as db_transaction
from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views import View
import reversion

from ..models import Conversation, Message, PendingWrite, Role
from .client import GENERIC_ERROR, converse
from .tools import KINDS


logger = logging.getLogger(__name__)


class AssistantView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Base das rotas do assistente: sessão, permissão e resposta em JSON."""

    permission_required = 'app.use_assistant'

    # Sem isto o Django mandaria quem não tem a permissão para a tela de login,
    # que num fetch vira o HTML do login dentro do chat. 403 é o que o front
    # sabe tratar.
    raise_exception = True

    def note(self, pending, text):
        """Conta ao modelo o que o clique do usuário decidiu.

        Vai como fala do usuário porque foi ele quem decidiu, e é assim que o
        modelo entende de quem partiu. Mas não aparece no chat: quem clicou
        acabou de ver o cartão mudar de estado, e repetir isso numa bolha é
        narrar de volta uma ação que a pessoa acabou de fazer.

        Sem esta mensagem o modelo segue achando o lançamento pendente, e a
        próxima coisa que ele diz é uma oferta de registrar o que já existe.
        """
        Message.objects.create(
            conversation=pending.conversation,
            role=Role.USER,
            content=text,
            items=[{'role': 'user', 'content': f'[{text}]'}],
            visible=False,
        )

    def conversation(self, create=True):
        """A conversa aberta do usuário, ou uma nova.

        Sempre filtrada pelo dono: uma conversa não é endereçável por id vindo
        do cliente justamente para não haver o que adivinhar.
        """
        existing = Conversation.objects.filter(user=self.request.user).first()
        if existing or not create:
            return existing
        return Conversation.objects.create(user=self.request.user)


class StreamView(AssistantView):
    """Recebe a mensagem do usuário e devolve a resposta conforme ela sai.

    É POST lido por fetch, e não EventSource, porque o EventSource só faz GET:
    a pergunta iria na URL, e não haveria como mandar o CSRF num cabeçalho. O
    corpo do evento segue o formato do SSE mesmo assim — é o que o nginx e o
    túnel já sabem não bufferizar.
    """

    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body or b'{}')
            text = (payload.get('message') or '').strip()
        except json.JSONDecodeError:
            text = ''

        if not text:
            return JsonResponse({'error': 'Escreva uma mensagem.'}, status=400)

        conversation = self.conversation()

        response = StreamingHttpResponse(
            self.events(conversation, request.user, text),
            content_type='text/event-stream; charset=utf-8',
        )
        # O nginx desta aplicação já está configurado para não bufferizar esta
        # rota; o cabeçalho cobre qualquer outro proxy no caminho, que é o caso
        # do túnel à frente dele.
        response['X-Accel-Buffering'] = 'no'
        response['Cache-Control'] = 'no-cache'
        return response

    def events(self, conversation, user, text):
        try:
            for event in converse(conversation, user, text):
                yield f'data: {json.dumps(event, ensure_ascii=False)}\n\n'
        except Exception:
            # A resposta já começou: não há status de erro para mandar, então o
            # que resta é avisar dentro do próprio stream. Sem isto o chat
            # ficaria com a bolinha girando para sempre.
            logger.exception('Stream da conversa %s morreu.', conversation.pk)
            yield f'data: {json.dumps({"type": "error", "message": GENERIC_ERROR})}\n\n'


class ConfirmView(AssistantView):
    """Grava o lançamento que o usuário confirmou.

    A validação é refeita, e não pulada por já ter passado: entre a proposta e o
    clique o cadastro pode ter mudado — uma conta desativada, uma regra de
    negócio alterada — e gravar por confiar na validação de antes seria aceitar
    o que a tela hoje recusaria.
    """

    http_method_names = ['post']

    def post(self, request, pk, *args, **kwargs):
        pending = PendingWrite.objects.filter(pk=pk, user=request.user).first()

        if pending is None:
            return JsonResponse({'error': 'Este lançamento não existe mais.'}, status=404)

        if not pending.is_open:
            return JsonResponse({'error': self.closed_message(pending)}, status=409)

        spec = KINDS[pending.kind]
        form = spec['form'](data=pending.payload, user=request.user)

        if not form.is_valid():
            pending.status = PendingWrite.Status.CANCELLED
            pending.resolved = timezone.now()
            pending.save(update_fields=['status', 'resolved'])
            return JsonResponse({
                'error': f'{spec["label"]} não pôde ser registrada: {self.first_error(form)}',
            }, status=422)

        # Tudo numa transação de banco: parcelamento e transferência geram
        # filhas no save, e uma falha no meio deixaria o registro de origem sem
        # as transações que o justificam.
        with db_transaction.atomic():
            with reversion.create_revision():
                reversion.set_user(request.user)
                reversion.set_comment('Criado pelo assistente, com confirmação do usuário.')
                instance = form.save()

            pending.status = PendingWrite.Status.CONFIRMED
            pending.resolved = timezone.now()
            pending.created_label = str(instance)
            pending.save(update_fields=['status', 'resolved', 'created_label'])

        # A confirmação entra no histórico como fala do usuário para o modelo não
        # continuar a conversa achando que o lançamento segue pendente — e não
        # oferecer registrar de novo o que já foi registrado.
        self.note(pending, f'O usuário confirmou o lançamento proposto. Ele FOI registrado: {instance}.')

        return JsonResponse({'status': 'confirmed', 'label': str(instance)})

    def closed_message(self, pending):
        if pending.status == PendingWrite.Status.CONFIRMED:
            return 'Este lançamento já foi registrado.'
        if pending.status == PendingWrite.Status.CANCELLED:
            return 'Este lançamento foi descartado.'
        return 'Este lançamento expirou. Peça de novo ao assistente.'

    def first_error(self, form):
        """A primeira mensagem de erro, em linguagem de gente.

        O cartão de confirmação não é lugar para nome de campo: quem clicou não
        montou o formulário, e "card: obrigatório" não diz nada a ele.
        """
        for messages in form.errors.values():
            if messages:
                return messages[0]
        return 'os dados não são mais válidos.'


class CancelView(AssistantView):
    """Descarta a proposta, a pedido do usuário."""

    http_method_names = ['post']

    def post(self, request, pk, *args, **kwargs):
        pending = PendingWrite.objects.filter(pk=pk, user=request.user, status=PendingWrite.Status.PENDING).first()

        if pending is None:
            return JsonResponse({'error': 'Este lançamento não está mais aberto.'}, status=404)

        pending.status = PendingWrite.Status.CANCELLED
        pending.resolved = timezone.now()
        pending.save(update_fields=['status', 'resolved'])

        self.note(pending, 'O usuário descartou o lançamento proposto. Ele NÃO foi registrado.')

        return JsonResponse({'status': 'cancelled'})


class HistoryView(AssistantView):
    """A conversa aberta, para o chat voltar como estava depois de um F5.

    Devolve uma lista só, em ordem de tempo, misturando mensagens e cartões de
    confirmação. Separá-las em duas listas — como estava — faria todo cartão
    reaparecer no fim da conversa ao recarregar, longe da frase que o pediu.
    """

    http_method_names = ['get']

    def get(self, request, *args, **kwargs):
        conversation = self.conversation(create=False)

        if conversation is None:
            return JsonResponse({'blocks': []})

        blocks = [
            {
                'kind': 'message',
                'role': message.role,
                'content': message.content,
                'at': message.created.isoformat(),
            }
            # Fora as de ferramenta, que são conversa entre o modelo e o sistema
            # — JSON de consulta na tela é o vazamento técnico que o prompt
            # proíbe —, e as invisíveis, que existem só para o modelo.
            for message in conversation.messages.filter(visible=True).exclude(role=Role.TOOL).exclude(content='')
        ] + [
            {
                'kind': 'pending',
                'id': pending.pk,
                'summary': pending.summary,
                # Cartão já resolvido volta sem botão, mostrando o que houve: o
                # usuário precisa reencontrar na conversa o lançamento que ele
                # confirmou, e não um buraco onde o cartão estava.
                'state': self.state(pending),
                'label': pending.created_label,
                'at': pending.created.isoformat(),
            }
            for pending in conversation.pending_writes.all()
        ]

        return JsonResponse({'blocks': sorted(blocks, key=lambda block: block['at'])})

    def state(self, pending):
        if pending.is_open:
            return 'open'
        if pending.status == PendingWrite.Status.PENDING:
            return 'expired'
        return pending.status


class ResetView(AssistantView):
    """Fecha a conversa atual e começa outra.

    Apagar em vez de arquivar é o que o usuário espera de um "limpar conversa":
    o histórico existe para ele reabrir o chat de onde parou, não para virar um
    arquivo que ele não pediu para manter.
    """

    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        Conversation.objects.filter(user=request.user).delete()
        return JsonResponse({'status': 'reset'})
