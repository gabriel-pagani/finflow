"""O prompt do sistema, montado das duas metades ao lado."""

from .documentation import DOCUMENTATION
from .identity import IDENTITY


def render(value, level=0):
    """A documentação como texto, para entrar no prompt.

    Vai em texto indentado, e não em JSON, porque o modelo lê isto como
    instrução, não como dado: chave entre aspas e chave de bloco só gastariam
    token sem dizer nada que a indentação já diga.
    """
    indent = '  ' * level
    if isinstance(value, dict):
        blocks = []
        for key, item in value.items():
            if isinstance(item, dict):
                blocks.append(f'{indent}{key}:\n{render(item, level + 1)}')
            else:
                blocks.append(f'{indent}{key}: {item}')
        return '\n'.join(blocks)
    return f'{indent}{value}'


def system_prompt(user, today):
    """O prompt completo, com as regras e o dia de hoje.

    A data entra aqui porque o modelo não tem relógio: sem ela, "este mês" e
    "ontem" viram o que ele imaginar, e o filtro sai de um período que o usuário
    não pediu.
    """
    return (
        f'{IDENTITY}\n'
        f'# DOCUMENTAÇÃO\n\n'
        f'{render(DOCUMENTATION)}\n\n'
        f'# CONTEXTO DESTA CONVERSA\n\n'
        f'Usuário: {user.get_short_name() or user.get_username()}\n'
        f'Data de hoje: {today.isoformat()} ({today.strftime("%d/%m/%Y")})\n'
        f'Fuso horário: America/Sao_Paulo\n'
    )


__all__ = ['DOCUMENTATION', 'IDENTITY', 'render', 'system_prompt']
