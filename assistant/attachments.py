import base64
from dataclasses import dataclass

from django.core.files.base import ContentFile

from .models import Attachment, AttachmentKind


class UploadError(Exception):
    pass


# Aceita pelos primeiros bytes, e não pelo content_type do formulário, que é
# rótulo escrito por quem envia.
SIGNATURES = [
    (AttachmentKind.IMAGE, 'image/jpeg', '.jpg', lambda head: head.startswith(b'\xff\xd8\xff')),
    (AttachmentKind.IMAGE, 'image/png', '.png', lambda head: head.startswith(b'\x89PNG\r\n\x1a\n')),
    (AttachmentKind.IMAGE, 'image/webp', '.webp', lambda head: head.startswith(b'RIFF') and head[8:12] == b'WEBP'),
    (AttachmentKind.AUDIO, 'audio/webm', '.webm', lambda head: head.startswith(b'\x1aE\xdf\xa3')),
    (AttachmentKind.AUDIO, 'audio/ogg', '.ogg', lambda head: head.startswith(b'OggS')),
    (AttachmentKind.AUDIO, 'audio/mp4', '.m4a', lambda head: head[4:8] == b'ftyp'),
    (AttachmentKind.AUDIO, 'audio/wav', '.wav', lambda head: head.startswith(b'RIFF') and head[8:12] == b'WAVE'),
    (AttachmentKind.AUDIO, 'audio/mpeg', '.mp3', lambda head: head.startswith(b'ID3') or head[:2] == b'\xff\xfb'),
]

LIMITS = {
    AttachmentKind.IMAGE: 8 * 1024 * 1024,
    AttachmentKind.AUDIO: 20 * 1024 * 1024,
}

# No turno guardado a imagem é o id do anexo, e só vira bytes na hora de montar
# o que vai para a API: base64 no banco seria reenviado a cada mensagem.
REFERENCE = 'attachment:'

FORGOTTEN = {
    'type': 'input_text',
    'text': '[o usuário enviou uma foto neste ponto da conversa; a imagem não está mais anexada]',
}


@dataclass
class Upload:
    kind: str
    mime: str
    extension: str
    data: bytes

    @property
    def name(self):
        return f'anexo{self.extension}'


def inspect(upload):
    data = upload.read()
    if not data:
        raise UploadError('O arquivo chegou vazio.')

    head = data[:16]
    for kind, mime, extension, matches in SIGNATURES:
        if matches(head):
            if len(data) > LIMITS[kind]:
                raise UploadError(f'O arquivo tem mais de {LIMITS[kind] // (1024 * 1024)} MB. Mande um menor.')
            return Upload(kind=kind, mime=mime, extension=extension, data=data)

    raise UploadError('Formato não aceito. Mande uma foto (JPEG, PNG ou WebP) ou um áudio.')


def attach(message, upload):
    attachment = Attachment(message=message, kind=upload.kind, mime=upload.mime)
    attachment.file.save(upload.name, ContentFile(upload.data), save=False)
    attachment.save()
    return attachment


def user_item(text, attachment=None):
    if attachment is None or attachment.kind != AttachmentKind.IMAGE:
        return {'role': 'user', 'content': text}

    content = [{'type': 'input_text', 'text': text}] if text else []
    # Comprovante é letra miúda, e a leitura automática pode escolher a barata,
    # que é a que erra centavo.
    content.append({'type': 'input_image', 'image_url': f'{REFERENCE}{attachment.pk}', 'detail': 'high'})
    return {'role': 'user', 'content': content}


def is_reference(part):
    return isinstance(part, dict) and part.get('type') == 'input_image' and str(part.get('image_url', '')).startswith(REFERENCE)


def has_image(items):
    return any(
        is_reference(part)
        for item in items
        if isinstance(item, dict) and isinstance(item.get('content'), list)
        for part in item['content']
    )


# O dono entra na busca junto do id: a referência é escrita pelo servidor, mas
# quem lê o turno guardado não tem como saber disso, e anexo é documento
# financeiro.
def embed(part, user):
    attachment = Attachment.objects.filter(
        pk=part['image_url'][len(REFERENCE):],
        message__conversation__user=user,
    ).first()
    if attachment is None:
        return FORGOTTEN

    try:
        with attachment.file.open('rb') as handle:
            data = handle.read()
    except (OSError, ValueError):
        return FORGOTTEN

    return {**part, 'image_url': f'data:{attachment.mime};base64,{base64.b64encode(data).decode("ascii")}'}


def resolve(items, user, inline):
    resolved = []
    for item in items:
        content = item.get('content') if isinstance(item, dict) else None
        if not isinstance(content, list):
            resolved.append(item)
            continue
        parts = [(embed(part, user) if inline else FORGOTTEN) if is_reference(part) else part for part in content]
        resolved.append({**item, 'content': parts})
    return resolved
