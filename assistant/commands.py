import re

from .models import Command


# Só no começo da mensagem a barra chama um comando: "1/2 do aluguel" é conversa.
CALL = re.compile(r'/([a-z0-9-]+)(?=\s|$)', re.IGNORECASE)


class CommandError(Exception):
    pass


def find(user, text):
    match = CALL.match(text)
    if match is None:
        return None

    name = match[1].lower()
    command = Command.objects.filter(user=user, name=name).first()
    if command is None:
        raise CommandError(f'Você não tem o comando /{name}. Crie-o em Comandos, na página do assistente.')
    return command


# O turno guarda as instruções como estavam na chamada: editar o comando depois
# não reescreve o que o modelo já respondeu.
def expand(command, text):
    complement = text[len(command.name) + 1:].strip()

    parts = [
        f'[Comando /{command.name}] Instruções que salvei para este comando. Siga-as agora e me responda com o resultado.',
        f'<instrucoes>\n{command.instructions}\n</instrucoes>',
    ]
    if complement:
        parts.append(f'Complemento: {complement}')
    return '\n\n'.join(parts)
