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

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction as db_transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
import reversion

from ..models import Attachment, Conversation, Message, PendingWrite, Role
from . import attachments
from .client import GENERIC_ERROR, converse
from .tools import KINDS


logger = logging.getLogger(__name__)


# O prefixo que o nginx conhece como área interna. Não é rota do Django: ele
# aparece só num cabeçalho de resposta, e o nginx o troca pelo arquivo em disco.
ACCEL_PREFIX = '/protected-media/'


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

    O corpo é multipart, e não JSON, desde que a mensagem passou a poder vir com
    uma foto ou um áudio junto. Um arquivo em JSON teria de ir em base64, o que
    o inflaria em um terço para ser desfeito do lado de cá.

    O arquivo é conferido antes de a resposta começar. Depois do primeiro byte
    não há mais status para devolver, e "esse formato não serve" é justamente o
    tipo de recado que precisa chegar como recusa, não como bolha de erro no
    meio de uma conversa que já começou.
    """

    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        text = (request.POST.get('message') or '').strip()
        upload = request.FILES.get('file')

        try:
            media = attachments.inspect(upload) if upload else None
        except attachments.UploadError as error:
            return JsonResponse({'error': str(error)}, status=400)

        if not text and media is None:
            return JsonResponse({'error': 'Escreva uma mensagem.'}, status=400)

        conversation = self.conversation()

        response = StreamingHttpResponse(
            self.events(conversation, request.user, text, media),
            content_type='text/event-stream; charset=utf-8',
        )
        # O nginx desta aplicação já está configurado para não bufferizar esta
        # rota; o cabeçalho cobre qualquer outro proxy no caminho, que é o caso
        # do túnel à frente dele.
        response['X-Accel-Buffering'] = 'no'
        response['Cache-Control'] = 'no-cache'
        return response

    def events(self, conversation, user, text, media=None):
        try:
            for event in converse(conversation, user, text, media):
                yield f'data: {json.dumps(event, ensure_ascii=False)}\n\n'
        except Exception:
            # A resposta já começou: não há status de erro para mandar, então o
            # que resta é avisar dentro do próprio stream. Sem isto o chat
            # ficaria com a bolinha girando para sempre.
            logger.exception('Stream da conversa %s morreu.', conversation.pk)
            yield f'data: {json.dumps({"type": "error", "message": GENERIC_ERROR})}\n\n'


class AttachmentView(AssistantView):
    """O arquivo que o usuário mandou, servido só para ele.

    Não é rota de mídia pública, e o endereço não é a chave: um comprovante é
    documento financeiro, então quem pede precisa ser o dono da conversa em que
    o anexo entrou. Isso é uma consulta, não um caminho que se adivinha.

    Quem entrega os bytes é o nginx, pelo X-Accel-Redirect. O Django decide se
    pode e devolve o endereço interno; o worker sai da frente em vez de segurar
    a conexão empurrando uma foto. No runserver não há nginx para obedecer ao
    cabeçalho, e lá o arquivo sai daqui mesmo.
    """

    http_method_names = ['get']

    def get(self, request, pk, *args, **kwargs):
        attachment = Attachment.objects.filter(pk=pk, message__conversation__user=request.user).first()

        if attachment is None:
            raise Http404

        if settings.DEBUG:
            return FileResponse(attachment.file.open('rb'), content_type=attachment.mime)

        response = HttpResponse(content_type=attachment.mime)
        response['X-Accel-Redirect'] = f'{ACCEL_PREFIX}{attachment.file.name}'

        # Cache no navegador, nunca num proxy compartilhado: o arquivo é de uma
        # pessoa só, e o endereço não muda enquanto o anexo existir.
        response['Cache-Control'] = 'private, max-age=604800'

        return response


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


class ChatView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """A página do assistente: o mesmo chat do atalho, ocupando o conteúdo.

    Não herda de AssistantView porque aquela base responde em JSON e devolve 403
    a quem não tem a permissão — o certo para um fetch, e não para alguém que
    digitou o endereço. Aqui quem não fez login vai para a tela de login, como
    em qualquer outra página, e só quem entrou sem a permissão leva o 403.

    O `assistant_page` diz ao global.html para não incluir o atalho flutuante:
    nesta página ele abriria por cima de um chat que já está aberto.
    """

    template_name = 'app/assistant.html'
    permission_required = 'app.use_assistant'
    raise_exception = True
    extra_context = {'assistant_page': True}


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

        # Fora as de ferramenta, que são conversa entre o modelo e o sistema —
        # JSON de consulta na tela é o vazamento técnico que o prompt proíbe —, e
        # as invisíveis, que existem só para o modelo. Mensagem vazia só é
        # descartada se também não tiver anexo: uma foto mandada sem legenda é
        # uma mensagem sem texto, e sumiria daqui.
        messages = (
            conversation.messages
            .filter(visible=True)
            .exclude(role=Role.TOOL)
            .exclude(Q(content='') & Q(attachment__isnull=True))
            .select_related('attachment')
        )

        blocks = [
            {
                'kind': 'message',
                'role': message.role,
                'content': message.content,
                'attachment': self.attachment(message),
                'at': message.created.isoformat(),
            }
            for message in messages
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

    def attachment(self, message):
        """O anexo da mensagem como o chat precisa dele: o que é, e onde está."""
        attachment = getattr(message, 'attachment', None)

        if attachment is None:
            return None

        return {
            'kind': attachment.kind,
            'url': reverse('app:assistant_attachment', args=[attachment.pk]),
        }

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
