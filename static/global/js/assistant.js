/* Chat do assistente ------------------------------------------------------ */

/* O stream é lido por fetch, e não por EventSource: o EventSource só faz GET, e
   a pergunta precisa ir no corpo, com o CSRF num cabeçalho. O formato dos
   eventos continua o do SSE, que é o que o nginx e o túnel já sabem entregar
   sem bufferizar. */

function setupAssistant(root) {
    const panel = root.querySelector('.assistant-panel');
    const toggle = root.querySelector('.assistant-toggle');
    const list = root.querySelector('.assistant-messages');
    const form = root.querySelector('.assistant-composer');
    const input = form.querySelector('textarea');

    const urls = root.dataset;
    let loaded = false;

    /* Rotas com id: o template gera a URL com 0 no lugar e a gente troca. Assim
       o caminho continua saindo do urls.py, e não montado à mão no JS. */
    function pendingUrl(template, id) {
        return template.replace(/0\/([a-z]+)\/$/, `${id}/$1/`);
    }

    function post(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': urls.csrf},
            body: JSON.stringify(body || {}),
        });
    }

    function scroll() {
        list.scrollTop = list.scrollHeight;
    }

    /* O modelo escreve Markdown: **negrito**, listas, quebras. Renderizar com
       textContent mostra os asteriscos crus; usar innerHTML com o texto dele
       seria injetar no DOM o que um modelo escreveu a partir de uma descrição
       de transação que o usuário digitou.

       Então o texto é escapado primeiro e só depois recebe as marcações que
       este renderizador conhece. A CSP é script-src 'self', então nenhuma
       biblioteca de Markdown de CDN carregaria mesmo — e para negrito, itálico,
       código e lista, ela seria bem mais peso do que isto. */
    function escapeHtml(text) {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function renderMarkdown(text) {
        const lines = escapeHtml(text).split('\n');
        const html = [];
        let list = null;

        function closeList() {
            if (list) {
                html.push(`<${list.tag}>${list.items.join('')}</${list.tag}>`);
                list = null;
            }
        }

        lines.forEach((line) => {
            const bullet = line.match(/^\s*[-*]\s+(.*)$/);
            const numbered = line.match(/^\s*\d+\.\s+(.*)$/);
            const tag = bullet ? 'ul' : (numbered ? 'ol' : null);

            if (tag) {
                if (!list || list.tag !== tag) {
                    closeList();
                    list = {tag: tag, items: []};
                }
                list.items.push(`<li>${inline((bullet || numbered)[1])}</li>`);
                return;
            }

            closeList();
            if (line.trim()) html.push(`<p>${inline(line)}</p>`);
        });

        closeList();
        return html.join('');
    }

    /* Negrito antes de itálico: o ** de **negrito** também casa com o * do
       itálico, e na ordem inversa sobraria um asterisco solto no meio da frase.
       O código vem antes dos dois para um * dentro de `crase` não virar marcação. */
    function inline(text) {
        return text
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
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

    /* Durante o stream o texto chega pela metade, e uma marcação pode estar
       aberta e ainda não fechada. Reprocessar o acumulado inteiro a cada delta
       mantém a bolha correta sem precisar adivinhar onde a marcação termina. */
    function appendDelta(node, text) {
        node.dataset.raw = (node.dataset.raw || '') + text;
        node.innerHTML = renderMarkdown(node.dataset.raw);
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

    /* Cartão de confirmação. É o único ponto do chat que grava, então ele é
       montado aqui a partir do resumo que o servidor já resolveu — o front não
       recalcula valor nem data, só mostra o que será gravado. */
    function pendingCard(id, summary, state, label) {
        const card = document.createElement('div');
        card.className = 'assistant-pending';

        const title = document.createElement('h4');
        title.textContent = `Confirmar ${summary.label.toLowerCase()}`;
        card.appendChild(title);

        const dl = document.createElement('dl');
        summary.rows.forEach((row) => {
            const dt = document.createElement('dt');
            dt.textContent = row.label;
            const dd = document.createElement('dd');
            dd.textContent = row.value;
            dl.append(dt, dd);
        });
        card.appendChild(dl);

        if (summary.note) {
            const note = document.createElement('p');
            note.className = 'note';
            note.textContent = summary.note;
            card.appendChild(note);
        }

        /* Cartão já resolvido volta sem botão: ele fica na conversa para o
           usuário reencontrar o lançamento que confirmou, não para agir de novo
           sobre algo que já virou — ou deixou de virar — dinheiro no banco. */
        if (state && state !== 'open') {
            const done = document.createElement('p');
            done.className = 'resolved';
            done.textContent = {
                confirmed: label ? `Registrado: ${label}` : 'Registrado.',
                cancelled: 'Descartado.',
                expired: 'Expirou sem confirmação.',
            }[state] || 'Resolvido.';
            card.appendChild(done);
            list.appendChild(card);
            scroll();
            return;
        }

        const footer = document.createElement('footer');
        const confirm = document.createElement('button');
        confirm.type = 'button';
        confirm.textContent = 'Confirmar';
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = 'secondary';
        cancel.textContent = 'Descartar';
        footer.append(confirm, cancel);
        card.appendChild(footer);

        function resolve(text) {
            footer.remove();
            const done = document.createElement('p');
            done.className = 'resolved';
            done.textContent = text;
            card.appendChild(done);
            scroll();
        }

        async function act(url, pendingText, doneText) {
            footer.querySelectorAll('button').forEach((button) => { button.disabled = true; });
            try {
                const response = await post(url);
                const data = await response.json();
                if (!response.ok) {
                    footer.querySelectorAll('button').forEach((button) => { button.disabled = false; });
                    bubble('error', data.error || pendingText);
                    return;
                }
                resolve(data.label ? `${doneText} ${data.label}` : doneText);
            } catch (error) {
                footer.querySelectorAll('button').forEach((button) => { button.disabled = false; });
                bubble('error', 'Não foi possível concluir agora. Tente de novo.');
            }
        }

        confirm.addEventListener('click', () => act(
            pendingUrl(urls.pending, id),
            'Não foi possível registrar.',
            'Registrado:',
        ));
        cancel.addEventListener('click', () => act(
            pendingUrl(urls.cancel, id),
            'Não foi possível descartar.',
            'Descartado.',
        ));

        list.appendChild(card);
        scroll();
    }

    /* Um evento SSE por vez, a partir do que já chegou do corpo. O buffer existe
       porque um evento pode chegar partido entre dois pedaços da rede. */
    function* parse(buffer) {
        let index;
        while ((index = buffer.value.indexOf('\n\n')) !== -1) {
            const chunk = buffer.value.slice(0, index);
            buffer.value = buffer.value.slice(index + 2);
            const line = chunk.split('\n').find((part) => part.startsWith('data: '));
            if (line) {
                try {
                    yield JSON.parse(line.slice(6));
                } catch (error) {
                    /* Evento ilegível não derruba o resto do stream. */
                }
            }
        }
    }

    async function send(text) {
        panel.dataset.busy = 'true';
        bubble('user', text);
        status('Pensando...');

        let reply = null;

        try {
            const response = await fetch(urls.stream, {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': urls.csrf},
                body: JSON.stringify({message: text}),
            });

            if (!response.ok || !response.body) {
                clearStatus();
                bubble('error', 'Não consegui responder agora. Tente de novo em instantes.');
                return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            const buffer = {value: ''};

            while (true) {
                const {done, value} = await reader.read();
                if (done) break;
                buffer.value += decoder.decode(value, {stream: true});

                for (const event of parse(buffer)) {
                    if (event.type === 'delta') {
                        clearStatus();
                        if (!reply) reply = bubble('assistant', '');
                        appendDelta(reply, event.text);
                        scroll();
                    } else if (event.type === 'tool') {
                        reply = null;
                        status(event.name === 'registrar_lancamento' ? 'Montando o lançamento...' : 'Consultando suas finanças...');
                    } else if (event.type === 'pending') {
                        clearStatus();
                        pendingCard(event.id, event.summary, 'open');
                        reply = null;
                    } else if (event.type === 'error') {
                        clearStatus();
                        bubble('error', event.message);
                        reply = null;
                    }
                }
            }
        } catch (error) {
            bubble('error', 'A conexão caiu no meio da resposta. Tente de novo.');
        } finally {
            clearStatus();
            panel.dataset.busy = 'false';
            input.focus();
        }
    }

    async function load() {
        if (loaded) return;
        loaded = true;

        try {
            const response = await fetch(urls.history, {headers: {'X-Requested-With': 'fetch'}});
            if (!response.ok) return;
            const data = await response.json();

            const blocks = data.blocks || [];

            blocks.forEach((block) => {
                if (block.kind === 'pending') {
                    pendingCard(block.id, block.summary, block.state, block.label);
                } else {
                    bubble(block.role, block.content);
                }
            });

            if (!blocks.length) {
                bubble('empty', 'Pergunte sobre suas finanças ou peça para registrar um novo lançamento.');
            }
        } catch (error) {
            /* Sem histórico o chat ainda funciona: a conversa começa vazia. */
        }
    }

    function open() {
        panel.hidden = false;
        toggle.setAttribute('aria-expanded', 'true');
        load();
        input.focus();
    }

    function close() {
        panel.hidden = true;
        toggle.setAttribute('aria-expanded', 'false');
    }

    toggle.addEventListener('click', () => (panel.hidden ? open() : close()));
    root.querySelector('.assistant-close').addEventListener('click', close);

    root.querySelector('.assistant-reset').addEventListener('click', async () => {
        await post(urls.reset);
        list.replaceChildren();
        loaded = false;
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

    /* Enter envia, Shift+Enter quebra linha: é o que se espera de um chat, e
       o textarea sozinho faria o contrário. */
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
        if (event.key === 'Escape' && !panel.hidden) close();
    });
}

document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('assistant');
    if (root) setupAssistant(root);
});
