import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View
from django.views.generic import TemplateView

from .client import GENERIC_ERROR, converse
from .models import Conversation, Role


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

        return JsonResponse({'blocks': [
            {'kind': 'message', 'role': message.role, 'content': message.content}
            for message in messages
        ]})


class ResetView(AssistantView):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        Conversation.objects.filter(user=request.user).delete()
        return JsonResponse({'status': 'reset'})
