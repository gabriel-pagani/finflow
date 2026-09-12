from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Permission


@pytest.fixture(autouse=True)
def serving(settings):
    settings.SECURE_SSL_REDIRECT = False
    settings.STORAGES = {
        **settings.STORAGES,
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }


@pytest.fixture
def use_assistant(db):
    return Permission.objects.get(codename='use_assistant', content_type__app_label='assistant')


@pytest.fixture
def logged(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def allowed(logged, user, use_assistant):
    user.user_permissions.add(use_assistant)
    return logged


class FakeItem:
    def __init__(self, data):
        self.data = data

    def model_dump(self, exclude_none=False):
        return self.data


def text_turn(text):
    """Os eventos de uma resposta só com texto, como o stream da API os entrega."""
    return [
        SimpleNamespace(type='response.output_text.delta', delta=text),
        SimpleNamespace(type='response.completed', response=SimpleNamespace(output=[
            FakeItem({'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}),
        ])),
    ]


def tool_turn(name, arguments, call_id='call_1'):
    return [
        SimpleNamespace(type='response.completed', response=SimpleNamespace(output=[
            FakeItem({'type': 'function_call', 'name': name, 'arguments': arguments, 'call_id': call_id}),
        ])),
    ]


@pytest.fixture
def fake_openai(monkeypatch):
    """Troca o cliente da OpenAI por um que devolve as rodadas enfileiradas, em ordem."""
    calls = []
    turns = []

    class Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return iter(turns.pop(0))

    monkeypatch.setattr('assistant.client.client', lambda: SimpleNamespace(responses=Responses()))
    return SimpleNamespace(turns=turns, calls=calls, text_turn=text_turn, tool_turn=tool_turn)
