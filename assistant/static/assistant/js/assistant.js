/* Chat do assistente ------------------------------------------------------ */

/* O stream é lido por fetch, e não por EventSource: o EventSource só faz GET, e
   a mensagem precisa ir no corpo, com o CSRF num cabeçalho. */

function setupAssistant(root) {
    const panel = root.querySelector('.assistant-panel');
    const embedded = root.classList.contains('assistant-embedded');
    const toggle = root.querySelector('.assistant-toggle');
    const closeButton = root.querySelector('.assistant-close');
    const list = root.querySelector('.assistant-messages');
    const form = root.querySelector('.assistant-composer');
    const input = form.querySelector('textarea');
    const tray = form.querySelector('.assistant-attachment');
    const fileInput = form.querySelector('.assistant-file');
    const attachButton = form.querySelector('.assistant-attach');
    const recordButton = form.querySelector('.assistant-record');
    const urls = root.dataset;

    // O anexo escolhido ou gravado e ainda não enviado; um por mensagem.
    let attachment = null;
    // O gravador em curso; nulo é parado.
    let recorder = null;

    // Um cupom fotografado de perto é legível bem antes disso; o resto é tempo
    // de upload no 4G.
    const MAX_SIDE = 1600;

    // Em ordem de preferência: o Chrome grava webm, o Safari só mp4. O servidor
    // confere pelos bytes de qualquer jeito.
    const AUDIO_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];

    // Casa com o @media do CSS: no celular o foco automático sobe o teclado por
    // cima do que a pessoa abriu para ler.
    const MOBILE = window.matchMedia('(max-width: 768px)');

    const STATUS = {
        consultar_cadastros: 'Consultando o cadastro...',
        analisar_transacoes: 'Calculando...',
        listar_transacoes: 'Buscando transações...',
        consultar_saldo: 'Consultando o saldo...',
        propor_cartao: 'Montando a proposta...',
        propor_transacao: 'Montando a proposta...',
        propor_parcelamento: 'Montando a proposta...',
        propor_transferencia: 'Montando a proposta...',
    };

    const OUTCOMES = {
        confirmed: (result, action) => `${action === 'delete' ? 'Apagado' : 'Feito'}: ${result}`,
        cancelled: () => 'Descartado. Nada foi gravado.',
        failed: (result) => `Não foi gravado: ${result}`,
        expired: () => 'Expirou sem confirmação. Nada foi gravado.',
    };

    function post(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: {'X-CSRFToken': urls.csrf},
            body: body,
        });
    }

    function scroll() {
        list.scrollTop = list.scrollHeight;
    }

    // O texto do modelo é escapado antes de receber as poucas marcações que
    // este renderizador conhece: ele pode conter descrições digitadas pelo
    // usuário, e innerHTML cru injetaria isso no DOM.
    function escapeHtml(text) {
        return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // Negrito antes de itálico, senão o ** de **negrito** vira dois itálicos.
    function inline(text) {
        return text
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
    }

    function renderMarkdown(text) {
        const html = [];
        let open = null;

        function closeList() {
            if (open) {
                html.push(`<${open.tag}>${open.items.join('')}</${open.tag}>`);
                open = null;
            }
        }

        escapeHtml(text).split('\n').forEach((line) => {
            const bullet = line.match(/^\s*[-*]\s+(.*)$/);
            const numbered = line.match(/^\s*\d+\.\s+(.*)$/);
            const tag = bullet ? 'ul' : (numbered ? 'ol' : null);

            if (tag) {
                if (!open || open.tag !== tag) {
                    closeList();
                    open = {tag: tag, items: []};
                }
                open.items.push(`<li>${inline((bullet || numbered)[1])}</li>`);
                return;
            }

            closeList();
            if (line.trim()) html.push(`<p>${inline(line)}</p>`);
        });

        closeList();
        return html.join('');
    }

    function bubble(role, text, media) {
        const node = document.createElement('div');
        node.className = `assistant-message ${role}`;

        if (role === 'assistant') {
            node.dataset.raw = text;
            node.innerHTML = renderMarkdown(text);
        } else {
            // Parágrafo próprio porque o áudio chega sem texto, e a transcrição
            // só preenche depois.
            const paragraph = document.createElement('p');
            paragraph.className = 'text';
            paragraph.textContent = text || '';
            paragraph.hidden = !text;
            node.appendChild(paragraph);
        }

        if (media) node.appendChild(mediaNode(media));

        list.appendChild(node);
        scroll();
        return node;
    }

    function mediaNode(media) {
        if (media.kind === 'image') {
            const image = document.createElement('img');
            image.className = 'assistant-photo';
            image.src = media.url;
            image.alt = 'Foto enviada';
            image.title = 'Abrir em tamanho real';
            image.addEventListener('load', scroll);
            image.addEventListener('click', () => window.open(media.url, '_blank', 'noopener'));
            return image;
        }

        const audio = document.createElement('audio');
        audio.className = 'assistant-audio';
        audio.controls = true;
        audio.preload = 'metadata';
        audio.src = media.url;
        return audio;
    }

    function fillBubble(node, text) {
        const paragraph = node.querySelector('.text');
        paragraph.textContent = text;
        paragraph.hidden = !text;
        scroll();
    }

    // O acumulado é reprocessado a cada delta: uma marcação pode chegar aberta
    // num pedaço e fechada no seguinte.
    function appendDelta(node, text) {
        node.dataset.raw += text;
        node.innerHTML = renderMarkdown(node.dataset.raw);
        scroll();
    }

    // Rotas com id saem do template com 0 no lugar, e o segmento é trocado aqui.
    function proposalUrl(template, id) {
        return template.replace(/\/0\//, `/${id}/`);
    }

    // O card é montado só com o que o servidor resolveu: o front não recalcula
    // valor nem data, mostra o que será gravado.
    function proposalCard(id, summary, state, result) {
        const card = document.createElement('div');
        card.className = `assistant-proposal action-${summary.action}`;

        const title = document.createElement('h4');
        title.textContent = summary.title;
        card.appendChild(title);

        const rows = document.createElement('dl');
        summary.rows.forEach((entry) => {
            const label = document.createElement('dt');
            label.textContent = entry.label;
            const value = document.createElement('dd');
            if ('before' in entry) {
                value.className = 'changed';
                const before = document.createElement('s');
                before.textContent = entry.before;
                value.append(before, ` → ${entry.value}`);
            } else {
                value.textContent = entry.value;
            }
            rows.append(label, value);
        });
        card.appendChild(rows);

        summary.notes.forEach((text) => {
            const note = document.createElement('p');
            note.className = 'note';
            note.textContent = text;
            card.appendChild(note);
        });

        function finish(finalState, finalResult) {
            card.querySelectorAll('footer').forEach((node) => node.remove());
            const outcome = document.createElement('p');
            outcome.className = `outcome ${finalState}`;
            outcome.textContent = OUTCOMES[finalState](finalResult, summary.action);
            card.appendChild(outcome);
            scroll();
        }

        list.appendChild(card);

        if (state !== 'open') {
            finish(state, result);
            return;
        }

        const footer = document.createElement('footer');
        const confirm = document.createElement('button');
        confirm.type = 'button';
        confirm.className = summary.action === 'delete' ? 'danger' : '';
        confirm.textContent = 'Confirmar';
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = 'secondary';
        cancel.textContent = 'Descartar';
        footer.append(confirm, cancel);
        card.appendChild(footer);
        scroll();

        async function act(template) {
            footer.querySelectorAll('button').forEach((button) => { button.disabled = true; });
            try {
                const response = await post(proposalUrl(template, id));
                const data = await response.json();
                if (data.state && data.state !== 'open') {
                    finish(data.state, data.result);
                } else {
                    footer.querySelectorAll('button').forEach((button) => { button.disabled = false; });
                }
                if (!response.ok) bubble('error', data.error);
            } catch (error) {
                footer.querySelectorAll('button').forEach((button) => { button.disabled = false; });
                bubble('error', 'Não foi possível concluir agora. Tente de novo.');
            }
        }

        confirm.addEventListener('click', () => act(urls.confirm));
        cancel.addEventListener('click', () => act(urls.cancel));
    }

    function status(text) {
        clearStatus();
        const node = document.createElement('div');
        node.className = 'assistant-status';
        node.textContent = text;
        list.appendChild(node);
        scroll();
    }

    function clearStatus() {
        list.querySelectorAll('.assistant-status').forEach((node) => node.remove());
    }

    // Um evento pode chegar partido entre dois pedaços da rede, daí o buffer.
    function* parse(buffer) {
        let index;
        while ((index = buffer.value.indexOf('\n\n')) !== -1) {
            const chunk = buffer.value.slice(0, index);
            buffer.value = buffer.value.slice(index + 2);
            const line = chunk.split('\n').find((part) => part.startsWith('data: '));
            if (!line) continue;
            try {
                yield JSON.parse(line.slice(6));
            } catch (error) {
                // Evento ilegível não derruba o resto do stream.
            }
        }
    }

    function handle(event, state) {
        if (event.type === 'transcript') {
            fillBubble(state.sent, event.text);
            status('Pensando...');
        } else if (event.type === 'delta') {
            clearStatus();
            if (!state.reply) state.reply = bubble('assistant', '');
            appendDelta(state.reply, event.text);
        } else if (event.type === 'tool') {
            state.reply = null;
            status(STATUS[event.name] || 'Trabalhando...');
        } else if (event.type === 'proposal') {
            clearStatus();
            proposalCard(event.id, event.summary, event.state, '');
            state.reply = null;
        } else if (event.type === 'error') {
            clearStatus();
            bubble('error', event.message);
            state.reply = null;
        }
    }

    async function failure(response) {
        try {
            const data = await response.json();
            if (data && data.error) return data.error;
        } catch (error) {
            // Corpo que não é JSON não tem o que dizer.
        }
        return 'Não consegui responder agora. Tente de novo em instantes.';
    }

    function holdAttachment(kind, blob, name) {
        dropAttachment();
        attachment = {kind: kind, blob: blob, name: name, url: URL.createObjectURL(blob)};

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'assistant-remove secondary';
        remove.title = 'Remover o anexo';
        remove.setAttribute('aria-label', 'Remover o anexo');
        remove.textContent = '\u2715';
        remove.addEventListener('click', () => dropAttachment());

        tray.replaceChildren(mediaNode(attachment), remove);
        tray.hidden = false;
    }

    // `keep` depois do envio: a bolha passou a usar a mesma URL, e revogá-la
    // apagaria a foto que acabou de ser mandada.
    function dropAttachment(keep) {
        if (attachment && keep !== true) URL.revokeObjectURL(attachment.url);
        attachment = null;
        tray.replaceChildren();
        tray.hidden = true;
        fileInput.value = '';
    }

    // Reduz antes de subir e, de quebra, normaliza para JPEG o que o navegador
    // souber desenhar, como o HEIC do iPhone. O que ele não desenhar vai como
    // veio, e quem recusa é o servidor.
    function shrink(file) {
        return new Promise((resolve) => {
            const url = URL.createObjectURL(file);
            const image = new Image();

            image.addEventListener('load', () => {
                const scale = Math.min(1, MAX_SIDE / Math.max(image.width, image.height));
                const canvas = document.createElement('canvas');
                canvas.width = Math.round(image.width * scale);
                canvas.height = Math.round(image.height * scale);
                canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
                URL.revokeObjectURL(url);
                canvas.toBlob((blob) => resolve(blob || file), 'image/jpeg', 0.82);
            });

            image.addEventListener('error', () => {
                URL.revokeObjectURL(url);
                resolve(file);
            });

            image.src = url;
        });
    }

    // O nome do erro separa quem bloqueou o microfone de quem não tem um.
    function microphoneProblem(error) {
        const name = error && error.name;
        if (name === 'NotAllowedError' || name === 'SecurityError') {
            return 'O acesso ao microfone foi recusado. Libere o Microfone nas permissões do site e tente de novo.';
        }
        if (name === 'NotFoundError' || name === 'OverconstrainedError') {
            return 'Nenhum microfone encontrado neste aparelho.';
        }
        if (name === 'NotReadableError' || name === 'AbortError') {
            return 'O microfone está ocupado por outro programa. Feche quem está usando e tente de novo.';
        }
        return 'Não consegui usar o microfone.';
    }

    function showRecording(active) {
        form.dataset.recording = active ? 'true' : 'false';
        recordButton.title = active ? 'Parar a gravação' : 'Gravar um áudio';
        recordButton.setAttribute('aria-label', recordButton.title);
    }

    async function startRecording() {
        if (!window.MediaRecorder || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            bubble('error', 'Este navegador não grava áudio nesta página. Digite a mensagem ou mande uma foto.');
            return;
        }

        let stream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({audio: true});
        } catch (error) {
            console.error('Microfone recusado:', error);
            bubble('error', microphoneProblem(error));
            return;
        }

        const type = AUDIO_TYPES.find((candidate) => MediaRecorder.isTypeSupported(candidate));
        const chunks = [];
        recorder = new MediaRecorder(stream, type ? {mimeType: type} : undefined);

        recorder.addEventListener('dataavailable', (event) => {
            if (event.data && event.data.size) chunks.push(event.data);
        });

        recorder.addEventListener('stop', () => {
            // Sem parar as trilhas, o indicador de microfone segue aceso na aba.
            stream.getTracks().forEach((track) => track.stop());
            recorder = null;
            showRecording(false);

            const blob = new Blob(chunks, {type: chunks.length ? chunks[0].type : 'audio/webm'});
            if (blob.size) holdAttachment('audio', blob, 'audio');
        });

        recorder.start();
        showRecording(true);
    }

    async function send(text, media) {
        panel.dataset.busy = 'true';
        const sent = bubble('user', text, media);
        status(media && media.kind === 'audio' ? 'Transcrevendo o áudio...' : 'Pensando...');

        const body = new FormData();
        body.append('message', text);
        if (media) body.append('file', media.blob, media.name);

        try {
            const response = await post(urls.stream, body);

            if (!response.ok || !response.body) {
                clearStatus();
                bubble('error', await failure(response));
                return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            const buffer = {value: ''};
            const state = {reply: null, sent: sent};

            while (true) {
                const {done, value} = await reader.read();
                if (done) break;
                buffer.value += decoder.decode(value, {stream: true});
                for (const event of parse(buffer)) handle(event, state);
            }
        } catch (error) {
            bubble('error', 'A conexão caiu no meio da resposta. Tente de novo.');
        } finally {
            clearStatus();
            panel.dataset.busy = 'false';
            if (!MOBILE.matches) input.focus();
        }
    }

    function renderBlock(block) {
        if (block.kind === 'proposal') {
            proposalCard(block.id, block.summary, block.state, block.result);
        } else {
            bubble(block.role, block.content, block.attachment);
        }
    }

    // Rebuscada a cada abertura: a conversa mora no banco e pode ter andado em
    // outro aparelho. A lista só é trocada quando a resposta chega, para não
    // piscar vazia.
    async function load() {
        if (panel.dataset.busy === 'true') return;

        let data;
        try {
            const response = await fetch(urls.history);
            if (!response.ok) return;
            data = await response.json();
        } catch (error) {
            return;
        }

        list.replaceChildren();
        data.blocks.forEach(renderBlock);

        if (!data.blocks.length) {
            bubble('empty', 'Pergunte sobre suas finanças ou peça para lançar, editar ou apagar transações, parcelamentos, transferências e cartões. Dá para mandar foto do comprovante ou um áudio.');
        }
    }

    function open() {
        panel.hidden = false;
        toggle.setAttribute('aria-expanded', 'true');
        load();
        if (!MOBILE.matches) input.focus();
    }

    function close() {
        panel.hidden = true;
        toggle.setAttribute('aria-expanded', 'false');
    }

    // O teclado virtual cobre a janela sem encolhê-la, e só o visualViewport
    // enxerga a área que sobrou. Vai no <html> porque quem consome é o <body>.
    function trackViewport() {
        const viewport = window.visualViewport;
        if (!viewport) return;
        const fit = () => document.documentElement.style.setProperty('--assistant-viewport', `${viewport.height}px`);
        viewport.addEventListener('resize', fit);
        fit();
    }

    trackViewport();

    if (toggle) toggle.addEventListener('click', () => (panel.hidden ? open() : close()));
    if (closeButton) closeButton.addEventListener('click', close);
    if (embedded) load();

    root.querySelector('.assistant-reset').addEventListener('click', async () => {
        if (recorder) recorder.stop();
        dropAttachment();
        await post(urls.reset);
        list.replaceChildren();
        load();
    });

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const text = input.value.trim();
        // Foto sem legenda é mensagem: quem fotografa o cupom já disse o que queria.
        if ((!text && !attachment) || panel.dataset.busy === 'true') return;

        const media = attachment;
        input.value = '';
        input.style.height = 'auto';
        dropAttachment(true);
        send(text, media);
    });

    attachButton.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', async () => {
        const file = fileInput.files && fileInput.files[0];
        if (file) holdAttachment('image', await shrink(file), 'foto.jpg');
    });

    recordButton.addEventListener('click', () => {
        if (recorder) recorder.stop();
        else startRecording();
    });

    // Enter envia, Shift+Enter quebra a linha.
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !embedded && !panel.hidden) close();
    });
}

document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('assistant');
    if (root) setupAssistant(root);
});
