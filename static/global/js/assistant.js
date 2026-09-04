/* Chat do assistente ------------------------------------------------------ */

/* O stream é lido por fetch, e não por EventSource: o EventSource só faz GET, e
   a pergunta precisa ir no corpo, com o CSRF num cabeçalho. O formato dos
   eventos continua o do SSE, que é o que o nginx e o túnel já sabem entregar
   sem bufferizar. */

function setupAssistant(root) {
    const panel = root.querySelector('.assistant-panel');

    /* Embutido é a página do assistente: o painel já está na tela e não tem
       botão de abrir nem de fechar. Flutuante é o atalho do computador, onde
       eles existem. Daí os dois botões serem procurados e testados, e não
       assumidos. */
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

    /* O anexo em espera: já escolhido ou já gravado, ainda não enviado. É um de
       cada vez — o compositor manda uma mensagem com um anexo, não um álbum. */
    let attachment = null;

    /* O gravador em curso, quando há um. Serve de estado também: nulo é parado. */
    let recorder = null;

    /* Lado maior da foto depois da redução. Um cupom fotografado de perto é
       legível bem antes disso; o que 12 megapixels acrescentam é tempo de
       upload no 4G e token de leitura, não nitidez de valor impresso. */
    const MAX_SIDE = 1600;

    /* Em ordem de preferência. O Chrome grava webm/opus, o Safari só mp4 — e um
       navegador que não suporte nenhum dos dois grava no padrão dele, que o
       servidor confere pelos primeiros bytes de qualquer jeito. */
    const AUDIO_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];

    /* Largura a partir da qual a tela é de celular. Casa com o @media do CSS —
       os dois precisam concordar sobre o que é "celular", senão o JS decide
       poupar um teclado virtual que não existe, ou sobe um numa tela onde o
       painel é um canto da janela. */
    const MOBILE = window.matchMedia('(max-width: 768px)');

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

    function bubble(role, text, media) {
        const node = document.createElement('div');
        node.className = `assistant-message ${role}`;

        if (role === 'assistant') {
            node.dataset.raw = text;
            node.innerHTML = renderMarkdown(text);
        } else {
            /* O texto do usuário vai num parágrafo próprio, e não solto na
               bolha, porque agora pode haver uma foto embaixo dele — e porque
               um áudio chega sem texto nenhum, que só aparece quando a
               transcrição volta do servidor. */
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

    /* A miniatura da foto ou o player do áudio, dentro da bolha de quem mandou.
       A URL vem de um blob local logo depois do envio e da rota do servidor
       quando a conversa é recarregada — o elemento é o mesmo nos dois casos. */
    function mediaNode(media) {
        if (media.kind === 'image') {
            const image = document.createElement('img');
            image.className = 'assistant-photo';
            image.src = media.url;
            image.alt = 'Foto enviada';
            image.loading = 'lazy';
            image.title = 'Abrir em tamanho real';
            /* A foto entra na conversa depois de carregar, e o que estava no fim
               da rolagem sai de vista quando ela empurra o resto para baixo. */
            image.addEventListener('load', scroll);
            /* Miniatura de comprovante não se lê. Quem precisa conferir o valor
               impresso abre o arquivo, que é do tamanho que a câmera tirou. */
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

    /* O texto que faltava na bolha do áudio, quando a transcrição chega. */
    function fillBubble(node, text) {
        const paragraph = node.querySelector('.text');
        if (!paragraph) return;
        paragraph.textContent = text;
        paragraph.hidden = !text;
        scroll();
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

    /* Guarda o anexo escolhido e o mostra acima do campo. Trocar de anexo
       descarta o anterior: é um por mensagem. */
    function holdAttachment(kind, blob, name) {
        dropAttachment();
        attachment = {kind: kind, blob: blob, name: name, url: URL.createObjectURL(blob)};

        tray.replaceChildren(mediaNode(attachment));

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'assistant-remove secondary';
        remove.title = 'Remover o anexo';
        remove.setAttribute('aria-label', 'Remover o anexo');
        remove.textContent = '\u2715';
        /* Sem a seta, o handler receberia o evento no lugar de `keep`. */
        remove.addEventListener('click', () => dropAttachment());
        tray.appendChild(remove);

        tray.hidden = false;
    }

    /* `keep` é para depois do envio: a bolha da conversa passou a usar a mesma
       URL, e revogá-la ali apagaria a foto que acabou de ser mandada. */
    function dropAttachment(keep) {
        if (attachment && keep !== true) URL.revokeObjectURL(attachment.url);
        attachment = null;
        tray.replaceChildren();
        tray.hidden = true;
        /* Sem isto, escolher o mesmo arquivo de novo não dispara `change`. */
        fileInput.value = '';
    }

    /* A foto reduzida antes de subir. Além do tamanho, isto normaliza o
       formato: o que o navegador consegue desenhar sai daqui como JPEG,
       inclusive o HEIC que o iPhone entrega. O que ele não desenhar volta como
       veio, e quem recusa com uma frase legível é o servidor. */
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

    function recordingType() {
        if (!window.MediaRecorder) return null;
        return AUDIO_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) || '';
    }

    /* O que o navegador guardou sobre o microfone: 'granted', 'denied',
       'prompt' — ou nulo, onde a consulta não existe. Serve para separar quem
       bloqueou de vez de quem só fechou a caixinha sem responder. */
    async function microphoneState() {
        try {
            return (await navigator.permissions.query({name: 'microphone'})).state;
        } catch (error) {
            return null;
        }
    }

    /* Onde fica o botão que destrava a permissão. Muda de lugar conforme o
       lugar de onde o site foi aberto, e mandar procurar "o cadeado" em quem
       não tem barra de endereço é mandar procurar o que não está lá.

       O caso instalado é o mais traiçoeiro: pelo ícone da tela inicial não há
       barra de endereço nenhuma, e a tela de permissões que o Android mostra
       para o atalho lista só notificações — o microfone não é dele, é do
       navegador, e continua sendo pedido pelo site nas configurações do
       navegador. Quem liberar por lá libera para o ícone também: é a mesma
       origem, no mesmo navegador. */
    function unblockHint() {
        if (window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone) {
            return 'Você abriu o FinFlow pelo ícone instalado, que não tem barra de endereço — e a tela de permissões do Android, para ele, só mostra notificações. Abra o site numa aba normal do navegador, libere o Microfone ali pelo ícone à esquerda do endereço, e o ícone instalado passa a valer também.';
        }

        return MOBILE.matches
            ? 'Toque no ícone à esquerda do endereço do site, abra as permissões e libere o Microfone. Depois recarregue a página.'
            : 'Clique no cadeado à esquerda do endereço do site, libere o Microfone e recarregue a página.';
    }

    /* Por que o microfone não veio. O navegador diz isso no `name` do erro, e a
       diferença importa para quem está lendo: "autorize o acesso" é um conselho
       inútil para quem não tem microfone nenhum ligado, e mandaria a pessoa
       procurar uma permissão que já está concedida. */
    async function microphoneProblem(error) {
        const name = error && error.name;

        if (name === 'NotAllowedError' || name === 'SecurityError') {
            /* Bloqueado de vez, o navegador não pergunta mais: nem na próxima
               vez, nem se o botão for tocado de novo. Só destravando nas
               configurações do site. Quem apenas fechou o pedido continua
               podendo ser perguntado, e para esse a instrução é outra. */
            return (await microphoneState()) === 'denied'
                ? `O microfone está bloqueado para este site. ${unblockHint()}`
                : 'O pedido de permissão foi recusado ou fechado. Toque no microfone de novo e escolha Permitir.';
        }

        if (name === 'NotFoundError' || name === 'OverconstrainedError') {
            return 'Nenhum microfone encontrado neste aparelho.';
        }

        if (name === 'NotReadableError' || name === 'AbortError') {
            return 'O microfone está ocupado por outro programa. Feche quem está usando e tente de novo.';
        }

        return 'Não consegui usar o microfone.';
    }

    async function startRecording() {
        const type = recordingType();

        if (type === null) {
            bubble('error', 'Este navegador não grava áudio. Digite a mensagem ou mande uma foto.');
            return;
        }

        /* Fora de contexto seguro o navegador nem expõe o objeto, e a chamada
           estouraria como erro de programação em vez de erro de microfone. */
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            bubble('error', 'Este navegador não dá acesso ao microfone nesta página.');
            return;
        }

        let stream;

        try {
            stream = await navigator.mediaDevices.getUserMedia({audio: true});
        } catch (error) {
            /* A frase da tela é curta por regra; o motivo exato fica no console,
               que é onde se olha quando ela não basta. */
            console.error('Microfone recusado:', error);
            bubble('error', await microphoneProblem(error));
            return;
        }

        const chunks = [];
        recorder = new MediaRecorder(stream, type ? {mimeType: type} : undefined);

        recorder.addEventListener('dataavailable', (event) => {
            if (event.data && event.data.size) chunks.push(event.data);
        });

        recorder.addEventListener('stop', () => {
            /* Sem parar as trilhas, o indicador de microfone ligado fica aceso
               na aba depois que a gravação acabou. */
            stream.getTracks().forEach((track) => track.stop());
            recorder = null;
            showRecording(false);

            const blob = new Blob(chunks, {type: chunks.length ? chunks[0].type : 'audio/webm'});
            if (blob.size) holdAttachment('audio', blob, 'audio');
        });

        recorder.start();
        showRecording(true);
    }

    /* Qual ícone aparece é decisão do CSS, pelo mesmo atributo. Aqui ficam só o
       estado e o rótulo — que o leitor de tela lê, e o desenho não diz. */
    function showRecording(active) {
        form.dataset.recording = active ? 'true' : 'false';
        recordButton.title = active ? 'Parar a gravação' : 'Gravar um áudio';
        recordButton.setAttribute('aria-label', recordButton.title);
    }

    /* A recusa do servidor tem frase própria — formato não aceito, arquivo
       grande demais — e é ela que o usuário precisa ler, não uma genérica. */
    async function failure(response) {
        try {
            const data = await response.json();
            if (data && data.error) return data.error;
        } catch (error) {
            /* Corpo que não é JSON não tem nada a dizer. */
        }
        return 'Não consegui responder agora. Tente de novo em instantes.';
    }

    async function send(text, media) {
        panel.dataset.busy = 'true';
        const sent = bubble('user', text, media);
        status(media && media.kind === 'audio' ? 'Transcrevendo o áudio...' : 'Pensando...');

        /* Multipart mesmo sem anexo: um caminho só de envio é um caminho só
           para conferir, dos dois lados. */
        const body = new FormData();
        body.append('message', text);
        if (media) body.append('file', media.blob, media.name);

        let reply = null;

        try {
            const response = await fetch(urls.stream, {
                /* Sem Content-Type à mão: quem o escreve é o navegador, que
                   precisa anexar a fronteira do multipart junto. */
                method: 'POST',
                headers: {'X-CSRFToken': urls.csrf},
                body: body,
            });

            if (!response.ok || !response.body) {
                clearStatus();
                bubble('error', await failure(response));
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
                    } else if (event.type === 'transcript') {
                        /* A bolha do áudio subiu sem texto: o que foi dito só se
                           sabe depois da transcrição. */
                        fillBubble(sent, event.text);
                        status('Pensando...');
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
            /* No desktop o foco volta para o campo e a conversa continua sem
               tirar a mão do teclado. No celular, não: se a pessoa dispensou
               o teclado para ler a resposta, devolver o foco o traz de volta
               justamente por cima do que ela foi ler. */
            if (!MOBILE.matches) input.focus();
        }
    }

    /* A conversa é rebuscada a cada abertura do painel, e não uma vez por
       carregamento de página. Ela mora no banco e é achada pelo usuário, então é
       a mesma no note e no celular — e o painel deixado aberto num deles fica
       para trás assim que algo é dito no outro. Reabrir é o gesto mais próximo
       de "me mostra como está agora", e é barato: uma requisição por abertura.

       A lista só é trocada depois que a resposta chega. Limpar antes deixaria o
       painel vazio durante a requisição, piscando a conversa a cada abertura. */
    async function load() {
        /* No meio de uma resposta, não. As bolhas do stream são nós que o `send`
           ainda está preenchendo, e trocá-las por baixo dele faria o texto que
           ainda está chegando cair fora da tela. */
        if (panel.dataset.busy === 'true') return;

        let data;

        try {
            const response = await fetch(urls.history, {headers: {'X-Requested-With': 'fetch'}});
            if (!response.ok) return;
            data = await response.json();
        } catch (error) {
            /* Falhou a busca: fica o que já estava na tela, que é melhor do que
               uma conversa que some porque a rede oscilou. Na primeira abertura
               não havia nada mesmo, e o chat continua utilizável. */
            return;
        }

        const blocks = data.blocks || [];

        list.replaceChildren();

        blocks.forEach((block) => {
            if (block.kind === 'pending') {
                pendingCard(block.id, block.summary, block.state, block.label);
            } else {
                bubble(block.role, block.content, block.attachment);
            }
        });

        if (!blocks.length) {
            bubble('empty', 'Pergunte sobre suas finanças ou peça para registrar um novo lançamento.');
        }
    }

    function open() {
        panel.hidden = false;
        root.classList.add('is-open');
        toggle.setAttribute('aria-expanded', 'true');
        load();

        /* No celular o foco automático sobe o teclado na hora, que come metade
           do que acabou de ser aberto. Quem quer digitar toca no campo; quem
           abriu para ler a conversa não pediu o teclado. */
        if (!MOBILE.matches) input.focus();
    }

    function close() {
        panel.hidden = true;
        root.classList.remove('is-open');
        toggle.setAttribute('aria-expanded', 'false');
    }

    /* O teclado virtual não encolhe a janela — ele cobre parte dela, e nem o vh
       nem o dvh percebem. Só o visualViewport enxerga a área que sobrou. Sem
       isto o campo de digitar fica atrás do teclado exatamente quando alguém
       vai digitar. O valor vira variável CSS, consumida só pela regra de tela
       de celular; no desktop ela fica lá sem efeito.

       Vai no <html>, e não no #assistant: na página do assistente quem precisa
       da medida é o <body>, que está acima do painel na árvore e não enxergaria
       uma variável declarada dentro dele. */
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

    /* Na página, a conversa é buscada na carga: não há abertura de painel para
       disparar a busca, e chegar numa tela vazia até alguém digitar algo daria
       a impressão de que o histórico se perdeu. */
    if (embedded) load();

    root.querySelector('.assistant-reset').addEventListener('click', async () => {
        if (recorder) recorder.stop();
        dropAttachment();
        await post(urls.reset);
        /* Limpa na hora, sem esperar a busca: se ela não vier — ou não rodar,
           por haver uma resposta em curso —, a conversa apagada não pode
           continuar na tela como se existisse. */
        list.replaceChildren();
        load();
    });

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const text = input.value.trim();

        /* Uma foto sem legenda é mensagem: quem fotografa o cupom já disse o
           que queria. Vazio de verdade é não ter nem texto nem anexo. */
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
        if (!file) return;
        holdAttachment('image', await shrink(file), 'foto.jpg');
    });

    recordButton.addEventListener('click', () => {
        if (recorder) recorder.stop();
        else startRecording();
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

    /* Esc fecha o atalho, que é uma coisa aberta por cima da página. Na página
       do assistente não há o que fechar — Esc ali não faria nada além de tirar
       o chat da tela sem ter para onde ir. */
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !embedded && !panel.hidden) close();
    });
}

document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('assistant');
    if (root) setupAssistant(root);
});
