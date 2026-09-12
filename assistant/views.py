import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View
from django.views.generic import TemplateView

from . import proposals
from .client import GENERIC_ERROR, converse
from .models import Conversation, Proposal, Role


logger = logging.getLogger(__name__)


class AssistantView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'assistant.use_assistant'
    # 403 em vez do redirect para o login: num fetch, o redirect viraria o HTML
    # da tela de login dentro do chat.
    raise_exception = True


class PageView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = 'assistant/page.html'
    permission_required = 'assistant.use_assistant'
    extra_context = {'assistant_page': True}

    def handle_no_permission(self):
        self.raise_exception = self.request.user.is_authenticated
        return super().handle_no_permission()


class StreamView(AssistantView):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        text = (request.POST.get('message') or '').strip()
        if not text:
            return JsonResponse({'error': 'Escreva uma mensagem.'}, status=400)

        conversation, _ = Conversation.objects.get_or_create(user=request.user)

        response = StreamingHttpResponse(self.events(conversation, request.user, text), content_type='text/event-stream; charset=utf-8')
        response['X-Accel-Buffering'] = 'no'
        response['Cache-Control'] = 'no-cache'
        return response

    def events(self, conversation, user, text):
        try:
            for event in converse(conversation, user, text):
                yield f'data: {json.dumps(event, ensure_ascii=False)}\n\n'
        except Exception:
            logger.exception('Stream da conversa %s morreu.', conversation.pk)
            yield f'data: {json.dumps({"type": "error", "message": GENERIC_ERROR}, ensure_ascii=False)}\n\n'


class HistoryView(AssistantView):
    http_method_names = ['get']

    def get(self, request, *args, **kwargs):
        conversation = Conversation.objects.filter(user=request.user).first()
        if conversation is None:
            return JsonResponse({'blocks': []})

        messages = conversation.messages.filter(visible=True).exclude(role=Role.TOOL).exclude(content='')

        # Uma lista só, em ordem de tempo: o card volta ao lado da frase que o
        # pediu, e já resolvido volta sem botão, dizendo o que houve.
        blocks = [
            (message.created_at, {'kind': 'message', 'role': message.role, 'content': message.content})
            for message in messages
        ] + [
            (proposal.created_at, {'kind': 'proposal', 'id': proposal.pk, 'summary': proposal.summary, 'state': proposal.state, 'result': proposal.result})
            for proposal in conversation.proposals.all()
        ]

        return JsonResponse({'blocks': [block for _, block in sorted(blocks, key=lambda item: item[0])]})


class ResetView(AssistantView):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        Conversation.objects.filter(user=request.user).delete()
        return JsonResponse({'status': 'reset'})


class ProposalView(AssistantView):
    http_method_names = ['post']

    def post(self, request, pk, *args, **kwargs):
        proposal = Proposal.objects.filter(pk=pk, user=request.user).first()
        if proposal is None:
            return JsonResponse({'error': 'Esta proposta não existe.'}, status=404)

        try:
            result = self.resolve(proposal)
        except proposals.ProposalError as error:
            proposal.refresh_from_db()
            return JsonResponse({'error': str(error), 'state': proposal.state, 'result': proposal.result}, status=409)

        return JsonResponse({'state': proposal.state, 'result': result})


class ConfirmView(ProposalView):
    def resolve(self, proposal):
        return proposals.confirm(proposal)


class CancelView(ProposalView):
    def resolve(self, proposal):
        proposals.cancel(proposal)
        return ''
