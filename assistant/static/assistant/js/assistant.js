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
    const urls = root.dataset;

    // Casa com o @media do CSS: no celular o foco automático sobe o teclado por
    // cima do que a pessoa abriu para ler.
    const MOBILE = window.matchMedia('(max-width: 768px)');

    const STATUS = {
        consultar_cadastros: 'Consultando o cadastro...',
        analisar_transacoes: 'Calculando...',
        listar_transacoes: 'Buscando transações...',
        consultar_saldo: 'Consultando o saldo...',
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

    function bubble(role, text) {
        const node = document.createElement('div');
        node.className = `assistant-message ${role}`;

        if (role === 'assistant') {
            node.dataset.raw = text;
            node.innerHTML = renderMarkdown(text);
        } else {
            node.textContent = text;
        }

        list.appendChild(node);
        scroll();
        return node;
    }

    // O acumulado é reprocessado a cada delta: uma marcação pode chegar aberta
    // num pedaço e fechada no seguinte.
    function appendDelta(node, text) {
        node.dataset.raw += text;
        node.innerHTML = renderMarkdown(node.dataset.raw);
        scroll();
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
        if (event.type === 'delta') {
            clearStatus();
            if (!state.reply) state.reply = bubble('assistant', '');
            appendDelta(state.reply, event.text);
        } else if (event.type === 'tool') {
            state.reply = null;
            status(STATUS[event.name] || 'Trabalhando...');
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

    async function send(text) {
        panel.dataset.busy = 'true';
        bubble('user', text);
        status('Pensando...');

        const body = new FormData();
        body.append('message', text);

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
            const state = {reply: null};

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
        bubble(block.role, block.content);
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
            bubble('empty', 'Pergunte sobre suas finanças: gastos, saldo, categorias, cartões e o que mais precisar.');
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
        await post(urls.reset);
        list.replaceChildren();
        load();
    });

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const text = input.value.trim();
        if (!text || panel.dataset.busy === 'true') return;

        input.value = '';
        input.style.height = 'auto';
        send(text);
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
