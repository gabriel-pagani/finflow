"""O que o assistente grava: a conversa, o que foi dito, o que espera confirmação.

Eles moram no app principal, e não num app próprio, porque o assistente não é um
sistema à parte: ele lê e escreve as mesmas finanças, e separá-lo em outro
app_label só moveria tabela de lugar.
"""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from .choices import Role


class Conversation(models.Model):
    """Uma conversa do usuário com o assistente.

    O histórico fica no banco, e não na sessão, por duas razões. A conversa
    sobrevive a restart e a deploy, que num sistema que roda em container
    acontecem no meio de qualquer tarde; e o que o assistente respondeu antes de
    um lançamento ser confirmado continua legível depois — se um valor saiu
    errado, dá para ler onde ele veio.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversations', verbose_name='Usuário')
    title = models.CharField(max_length=120, blank=True, verbose_name='Título')
    created = models.DateTimeField(auto_now_add=True, verbose_name='Criada em')
    updated = models.DateTimeField(auto_now=True, verbose_name='Atualizada em')

    def __str__(self):
        return self.title or f'Conversa de {self.created:%d/%m/%Y %H:%M}'

    class Meta:
        ordering = ['-updated']
        verbose_name = 'Conversa'
        verbose_name_plural = 'Conversas'

        # A permissão mora aqui porque o Django precisa pendurá-la em algum
        # modelo, e este é o que só existe por causa do assistente. Ela guarda o
        # botão flutuante e as rotas do chat: sem ela, o botão não é renderizado
        # e as views respondem 403.
        permissions = [
            ('use_assistant', 'Pode usar o assistente'),
        ]

class Message(models.Model):
    """Um trecho da conversa: o que a tela mostra e o que o modelo relê.

    Os dois não são a mesma coisa, e por isso são campos diferentes. `content` é
    texto para o usuário. `items` é a lista de itens exatamente como a API os
    devolveu e os espera de volta — texto, chamada de ferramenta e, num modelo de
    raciocínio, os itens de raciocínio que sustentam a chamada.

    Guardar os itens crus, e não uma tradução deles, é o que mantém o raciocínio
    inteiro entre uma rodada de ferramenta e a seguinte: reescrevê-los num formato
    próprio significaria descartar o que não coubesse no formato, e o que não
    couber é justamente o que o modelo usaria para não repetir a consulta.

    Uma linha guarda um turno inteiro, e não um item por linha, porque os itens de
    um turno precisam voltar juntos e na ordem: um raciocínio separado da chamada
    que ele justifica faz a API recusar a conversa toda.

    O que NÃO é guardado é o system prompt: ele é remontado a cada requisição,
    porque carrega a data de hoje. Um prompt gravado envelheceria junto com a
    conversa, e "este mês" passaria a significar o mês em que ela começou.
    """

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages', verbose_name='Conversa')
    role = models.CharField(max_length=20, choices=Role.choices, verbose_name='Papel')
    content = models.TextField(blank=True, verbose_name='Conteúdo')

    items = models.JSONField(default=list, blank=True, verbose_name='Itens da Conversa')

    # Nem tudo que o modelo precisa ler é coisa que o usuário precisa ver. O
    # resultado de uma confirmação é o exemplo: o modelo tem de saber que o
    # lançamento foi gravado, senão oferece registrar de novo o que acabou de
    # ser registrado — mas quem clicou no botão não precisa que a própria ação
    # dele seja narrada de volta, em terceira pessoa, como se ele a tivesse
    # digitado.
    visible = models.BooleanField(default=True, verbose_name='Aparece no Chat')

    created = models.DateTimeField(auto_now_add=True, verbose_name='Criada em')

    def __str__(self):
        return f'{self.get_role_display()}: {self.content[:60]}'

    class Meta:
        ordering = ['created', 'id']
        verbose_name = 'Mensagem'
        verbose_name_plural = 'Mensagens'
class PendingWrite(models.Model):
    """Um lançamento montado pelo assistente, à espera do usuário confirmar.

    É o que separa "o modelo propôs" de "o dinheiro foi gravado". A ferramenta de
    registro valida o lançamento no mesmo formulário da tela e para aqui; quem
    grava é o clique, num POST próprio, com CSRF e com o usuário da sessão.

    O registro também é o que impede o replay: ele é consumido na primeira
    confirmação e carrega o dono, então confirmar o pendente de outra pessoa não
    é uma checagem que alguém possa esquecer de escrever — é um filtro que não
    encontra linha nenhuma.
    """

    # Uma proposta velha não deve poder ser confirmada: entre montá-la e clicar,
    # o saldo mudou, a fatura virou, e o usuário já não lembra do que se tratava.
    EXPIRY = timedelta(hours=1)

    class Status(models.TextChoices):
        PENDING = 'pending', 'Aguardando confirmação'
        CONFIRMED = 'confirmed', 'Confirmado'
        CANCELLED = 'cancelled', 'Cancelado'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pending_writes', verbose_name='Usuário')
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='pending_writes', verbose_name='Conversa')

    kind = models.CharField(max_length=20, verbose_name='Tipo de Lançamento')

    # O que vai para o formulário na confirmação, exatamente como ele validou na
    # proposta. Regravar a partir do texto do chat abriria espaço para o valor
    # confirmado ser diferente do valor mostrado.
    payload = models.JSONField(verbose_name='Dados do Lançamento')

    # O que a tela mostra no cartão: rótulos já resolvidos, para o front não
    # precisar consultar conta, categoria e cartão de novo só para escrever.
    summary = models.JSONField(verbose_name='Resumo Exibido')

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name='Situação')
    created = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    resolved = models.DateTimeField(blank=True, null=True, verbose_name='Resolvido em')

    # O que a confirmação gerou. Não é ForeignKey porque o alvo é um de três
    # modelos, e uma chave genérica custaria uma tabela de contenttypes para
    # guardar o que só se usa como rótulo na tela.
    created_label = models.CharField(max_length=200, blank=True, verbose_name='Resultado')

    @property
    def is_expired(self):
        return timezone.now() - self.created > self.EXPIRY

    @property
    def is_open(self):
        return self.status == self.Status.PENDING and not self.is_expired

    def __str__(self):
        return f'{self.get_status_display()}: {self.kind} ({self.user})'

    class Meta:
        ordering = ['-created']
        verbose_name = 'Lançamento Pendente'
        verbose_name_plural = 'Lançamentos Pendentes'
def attachment_path(instance, filename):
    """O caminho do arquivo no disco: pasta do dono, nome sorteado.

    O nome que veio do navegador não entra nisto. Ele é escolhido por quem
    envia, e nome de quem envia virando caminho no servidor é como uma foto de
    nota fiscal acaba gravada por cima de outra coisa. O que fica é um sorteio
    com a extensão que a inspeção do conteúdo confirmou.

    A pasta é a do usuário porque o arquivo é dele: separado assim, uma listagem
    de diretório já responde de quem é cada comprovante, e uma remoção de conta
    tem uma pasta para apagar em vez de uma busca para fazer.
    """
    return f'assistant/{instance.message.conversation.user_id}/{uuid4().hex}{Path(filename).suffix}'


class Attachment(models.Model):
    """A foto ou o áudio que o usuário mandou junto de uma mensagem.

    O arquivo fica em disco, e não no banco, e o que a mensagem guarda é uma
    referência a esta linha. São dois motivos. Uma foto de nota fiscal em base64
    dentro do JSON do turno voltaria para a API a cada mensagem seguinte da
    conversa, e engordaria o backup do Postgres com bytes que não são dado
    financeiro; e a miniatura que reaparece no chat depois de um F5 precisa de
    uma URL, que uma coluna JSON não tem como servir.

    Um anexo por mensagem: o compositor manda um arquivo de cada vez, e uma
    relação de um para um deixa isso explícito no schema em vez de deixá-lo como
    combinado entre o front e a view.

    O áudio não é lido pelo modelo. Ele é transcrito na chegada, e o que vai para
    a conversa é a transcrição — que fica em `content`, na mensagem. O arquivo
    permanece para o usuário poder ouvir de novo o que ele mesmo ditou, e para
    conferir a transcrição quando um número parecer errado.
    """

    class Kind(models.TextChoices):
        IMAGE = 'image', 'Imagem'
        AUDIO = 'audio', 'Áudio'

    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='attachment', verbose_name='Mensagem')
    kind = models.CharField(max_length=10, choices=Kind.choices, verbose_name='Tipo')
    file = models.FileField(upload_to=attachment_path, max_length=200, verbose_name='Arquivo')

    # O tipo confirmado pela inspeção do conteúdo, e não o rótulo que o
    # navegador mandou. É ele que sai no Content-Type de quem for baixar.
    mime = models.CharField(max_length=60, verbose_name='Tipo de Conteúdo')

    created = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')

    def __str__(self):
        return f'{self.get_kind_display()} de {self.message.conversation.user}'

    class Meta:
        verbose_name = 'Anexo'
        verbose_name_plural = 'Anexos'


@receiver(post_delete, sender=Attachment)
def remove_attachment_file(sender, instance, **kwargs):
    """Apaga o arquivo quando a linha sai.

    O Django não faz isso sozinho, e aqui a diferença importa: "Limpar a
    conversa" apaga a conversa em cascata, e sem isto os comprovantes ficariam
    no volume para sempre — o usuário teria mandado apagar e o disco continuaria
    guardando a foto dos gastos dele.
    """
    if instance.file:
        instance.file.delete(save=False)
