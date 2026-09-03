"""Foto e áudio: o que o navegador manda, o que o disco guarda, o que o modelo lê.

Os três são coisas diferentes, e é por isso que este módulo existe separado. O
que chega é um arquivo de origem desconhecida, que precisa ser conferido antes
de virar qualquer outra coisa. O que fica no disco é um arquivo com nome sorteado
numa pasta com dono. E o que o modelo lê é uma imagem em base64 dentro do turno —
que não é a mesma coisa que o turno guardado, porque a foto no banco seria peso
morto reenviado a cada mensagem seguinte.

O áudio não passa por aqui para ser lido pelo modelo: ele é transcrito, e o que
entra na conversa é o texto. O arquivo continua guardado para o usuário ouvir de
novo o que ditou quando um número da transcrição parecer errado.
"""

import base64
from dataclasses import dataclass

from django.core.files.base import ContentFile

from ..models import Attachment


class UploadError(Exception):
    """O arquivo não serve, e a mensagem é para o usuário ler."""


# O que o conteúdo precisa começar para ser aceito, e o que ele é quando começa
# assim. A checagem é pelos primeiros bytes, e não pelo `content_type` do
# formulário, porque esse é um rótulo escrito por quem envia: aceitar por ele é
# aceitar qualquer arquivo que se diga imagem. A extensão gravada sai daqui
# também, pelo mesmo motivo — o nome do arquivo é palavra de quem manda.
SIGNATURES = [
    (Attachment.Kind.IMAGE, 'image/jpeg', '.jpg', lambda head: head.startswith(b'\xff\xd8\xff')),
    (Attachment.Kind.IMAGE, 'image/png', '.png', lambda head: head.startswith(b'\x89PNG\r\n\x1a\n')),
    (Attachment.Kind.IMAGE, 'image/webp', '.webp', lambda head: head.startswith(b'RIFF') and head[8:12] == b'WEBP'),
    (Attachment.Kind.AUDIO, 'audio/webm', '.webm', lambda head: head.startswith(b'\x1aE\xdf\xa3')),
    (Attachment.Kind.AUDIO, 'audio/ogg', '.ogg', lambda head: head.startswith(b'OggS')),
    (Attachment.Kind.AUDIO, 'audio/mp4', '.m4a', lambda head: head[4:8] == b'ftyp'),
    (Attachment.Kind.AUDIO, 'audio/wav', '.wav', lambda head: head.startswith(b'RIFF') and head[8:12] == b'WAVE'),
    (Attachment.Kind.AUDIO, 'audio/mpeg', '.mp3', lambda head: head.startswith(b'ID3') or head[:2] == b'\xff\xfb'),
]

# Tetos por tipo. A foto chega já reduzida pelo navegador, e o teto aqui é para o
# que não passou por ele; o áudio é uma fala de alguém registrando uma compra, e
# vinte megabytes são minutos de conversa em qualquer codec que o navegador grave.
LIMITS = {
    Attachment.Kind.IMAGE: 8 * 1024 * 1024,
    Attachment.Kind.AUDIO: 20 * 1024 * 1024,
}

# Como a imagem aparece no turno guardado. O valor não é a imagem: é o número da
# linha que sabe onde ela está. Quem troca isto pelos bytes é `resolve`, na hora
# de montar o que vai para a API.
REFERENCE = 'attachment:'

# O que sobra de uma foto antiga no contexto. O modelo precisa saber que houve
# uma imagem ali — senão a própria resposta que ele deu a respeito dela fica sem
# antecedente — mas não precisa recebê-la de novo.
FORGOTTEN = {
    'type': 'input_text',
    'text': '[o usuário enviou uma foto neste ponto da conversa; a imagem não está mais anexada]',
}


@dataclass
class Upload:
    """Um arquivo já conferido, ainda não gravado."""

    kind: str
    mime: str
    extension: str
    data: bytes

    @property
    def name(self):
        return f'anexo{self.extension}'


def inspect(upload):
    """Confere o que chegou e devolve o arquivo já identificado.

    Levanta `UploadError` com uma frase pronta para a tela. O teto de tamanho é
    checado depois da assinatura de propósito: dizer "essa imagem é grande
    demais" sobre um arquivo que nem é imagem manda a pessoa comprimir o que ela
    deveria era trocar.
    """
    data = upload.read()

    if not data:
        raise UploadError('O arquivo chegou vazio.')

    head = data[:16]

    for kind, mime, extension, matches in SIGNATURES:
        if matches(head):
            if len(data) > LIMITS[kind]:
                limit = LIMITS[kind] // (1024 * 1024)
                raise UploadError(f'O arquivo tem mais de {limit} MB. Mande um menor.')
            return Upload(kind=kind, mime=mime, extension=extension, data=data)

    raise UploadError('Formato não aceito. Mande uma foto (JPEG, PNG ou WebP) ou um áudio.')


def attach(message, upload):
    """Grava o arquivo e prende a linha à mensagem."""
    attachment = Attachment(message=message, kind=upload.kind, mime=upload.mime)
    attachment.file.save(upload.name, ContentFile(upload.data), save=False)
    attachment.save()
    return attachment


def user_item(text, attachment=None):
    """O turno do usuário no formato que a API espera.

    Sem imagem, continua sendo o que sempre foi: papel e texto. Com imagem, o
    conteúdo vira lista de partes, e a parte da imagem carrega a referência, não
    os bytes.
    """
    if attachment is None or attachment.kind != Attachment.Kind.IMAGE:
        return {'role': 'user', 'content': text}

    content = []

    if text:
        content.append({'type': 'input_text', 'text': text})

    content.append({
        'type': 'input_image',
        'image_url': f'{REFERENCE}{attachment.pk}',
        # Comprovante é letra miúda: valor, data e estabelecimento saem em corpo
        # pequeno, e é exatamente o que precisa ser lido. No detalhe automático a
        # API pode escolher a leitura barata, que é a que erra centavo.
        'detail': 'high',
    })

    return {'role': 'user', 'content': content}


def is_reference(part):
    return (
        isinstance(part, dict)
        and part.get('type') == 'input_image'
        and str(part.get('image_url', '')).startswith(REFERENCE)
    )


def has_image(items):
    return any(
        is_reference(part)
        for item in items
        if isinstance(item, dict) and isinstance(item.get('content'), list)
        for part in item['content']
    )


def embed(part):
    """A referência trocada pela imagem em base64, ou o marcador se ela sumiu.

    O arquivo pode não estar mais lá — volume recriado sem os dados, remoção
    manual — e isso não é motivo para a conversa inteira parar de responder. O
    modelo recebe o marcador e segue com o texto, que é mais do que ele teria com
    uma exceção subindo daqui.
    """
    pk = part['image_url'][len(REFERENCE):]
    attachment = Attachment.objects.filter(pk=pk).first()

    if attachment is None:
        return FORGOTTEN

    try:
        with attachment.file.open('rb') as handle:
            data = handle.read()
    except (OSError, ValueError):
        return FORGOTTEN

    encoded = base64.b64encode(data).decode('ascii')

    return {**part, 'image_url': f'data:{attachment.mime};base64,{encoded}'}


def resolve(items, inline):
    """Os itens de um turno prontos para a API.

    `inline` decide se a foto daquele turno volta inteira ou vira marcador. Quem
    decide é o `history`, que sabe quais turnos são recentes; aqui só se aplica.
    """
    resolved = []

    for item in items:
        content = item.get('content') if isinstance(item, dict) else None

        if not isinstance(content, list):
            resolved.append(item)
            continue

        parts = [
            (embed(part) if inline else FORGOTTEN) if is_reference(part) else part
            for part in content
        ]

        resolved.append({**item, 'content': parts})

    return resolved
