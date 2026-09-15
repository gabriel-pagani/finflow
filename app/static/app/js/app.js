/* A inicialização é dirigida por data-attributes em vez de <script> inline
   para que a CSP possa recusar script inline sem 'unsafe-inline'. */

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-multiselect]').forEach(setupMultiselect);
    document.querySelectorAll('dialog.modal').forEach(setupDialogDismiss);
    setupLinkedFields();
    document.querySelectorAll('[data-record-urls]').forEach(setupRecordCrud);
    document.querySelectorAll('[data-bar-chart]').forEach(setupBarChart);
    document.querySelectorAll('[data-donut-chart]').forEach(setupDonutChart);
});

function readJsonScript(id) {
    const node = document.getElementById(id);
    return node ? JSON.parse(node.textContent) : null;
}

/* Multiselect ------------------------------------------------------------- */

function setupMultiselect(root) {
    const details = root.querySelector('details');
    const label = root.querySelector('[data-multiselect-label]');
    const empty = root.dataset.empty || 'Todos';
    const boxes = Array.from(root.querySelectorAll('input[type="checkbox"][name]'));
    const all = addSelectAll(root, boxes, empty);

    function updateLabel() {
        const checked = boxes.filter((box) => box.checked);
        const everything = checked.length === boxes.length;

        all.checked = checked.length > 0 && everything;
        all.indeterminate = checked.length > 0 && !everything;

        // Tudo marcado recorta o mesmo que nada marcado: as opções são só as
        // que estão em uso, então o rótulo e o destaque também são os mesmos.
        if (checked.length === 0 || everything) {
            label.textContent = empty;
        } else if (checked.length === 1) {
            label.textContent = checked[0].nextElementSibling.textContent.trim();
        } else {
            // 'a' para Conta/Categoria, 'o' para Tipo/Método
            const suffix = root.dataset.gender === 'f' ? 'as' : 'os';
            label.textContent = `${checked.length} selecionad${suffix}`;
        }

        root.classList.toggle('has-selection', checked.length > 0 && !everything);
    }

    boxes.forEach((box) => box.addEventListener('change', updateLabel));
    all.addEventListener('change', () => {
        boxes.forEach((box) => {
            box.checked = all.checked;
        });
        updateLabel();
    });

    // Clicar fora fecha o painel; sem isso vários ficariam abertos ao mesmo tempo.
    document.addEventListener('click', (event) => {
        if (details.open && !root.contains(event.target)) {
            details.open = false;
        }
    });

    details.addEventListener('toggle', () => {
        if (!details.open) return;
        document.querySelectorAll('[data-multiselect] details[open]').forEach((other) => {
            if (other !== details) other.open = false;
        });
    });

    updateLabel();
}

// Tirar uma opção só de uma lista longa pedia marcar todas as outras à mão.
// Esta caixa marca ou desmarca tudo de uma vez. Ela nasce pelo JS e sem name:
// não vai no envio, e sem script o filtro segue funcionando sem ela.
function addSelectAll(root, boxes, empty) {
    const wrapper = document.createElement('div');
    wrapper.className = 'multiselect-all';
    // Com uma opção só, marcar todas é marcar ela.
    wrapper.hidden = boxes.length < 2;

    const option = document.createElement('label');
    option.className = 'multiselect-option';

    const input = document.createElement('input');
    input.type = 'checkbox';

    const text = document.createElement('span');
    text.textContent = `Selecionar ${empty.toLowerCase()}`;

    option.append(input, text);
    wrapper.append(option);
    root.querySelector('.multiselect-panel').prepend(wrapper);

    return input;
}

/* Modais ------------------------------------------------------------------ */

// Clicar no fundo escuro fecha, comportamento que o <dialog> não dá de graça.
function setupDialogDismiss(dialog) {
    dialog.querySelectorAll('[data-modal-close]').forEach((button) => {
        button.addEventListener('click', () => dialog.close());
    });

    dialog.addEventListener('click', (event) => {
        if (event.target === dialog) dialog.close();
    });
}

/* Combinações válidas nos formulários ------------------------------------- */

// O modal oferece só o que o servidor aceitaria: quais tipos e métodos cada
// conta aceita vem das regras de negócio dela, cada método admite só algumas
// naturezas, só a natureza Normal recebe categoria e um cartão pertence a uma
// conta só. Isso poupa o usuário de montar uma transação impossível e só
// descobrir no envio; quem valida de verdade continua sendo o model.
const SYNC_OPTIONS = 'finflow:sync-options';

// Único método que tem cartão; nos outros o campo nem aparece.
const CREDIT = 'CREDIT';

// Única natureza que recebe categoria.
const REGULAR = 'REGULAR';

// A categoria entra na combinação só como "aceita qualquer uma" ou "nenhuma":
// as categorias são as mesmas para toda conta, e o que decide é a natureza.
const ANY_CATEGORY = '*';

function setupLinkedFields() {
    const options = readJsonScript('data-form-options');
    if (!options) return;

    document.querySelectorAll('[data-modal-form]').forEach((form) => linkFields(form, options));
}

function accountsAccepting(options, { type, method }) {
    return Object.keys(options.rules).filter((id) => (options.rules[id][type] || []).includes(method));
}

// Todas as combinações que o servidor aceitaria no formulário, cada uma com um
// valor por campo recortável. Em vez de encadear um campo no outro, o que só
// funciona numa ordem de preenchimento, cada campo passa a oferecer o que ainda
// cabe em alguma combinação junto do que já foi escolhido nos demais.
//
// O cartão fica fora das combinações: sem opção vazia, sempre haveria um
// marcado, e ele prenderia a conta à dele. O cartão segue a conta.
function combinationsFor(fields, card, fixed, options) {
    // Na transferência as duas pernas já nascem com tipo e método fixos: resta
    // escolher contas que aceitem cada lado, e que não sejam a mesma.
    if (fields.origin && fields.destination) {
        const combinations = [];
        accountsAccepting(options, fixed.origin).forEach((origin) => {
            accountsAccepting(options, fixed.destination).forEach((destination) => {
                if (origin !== destination) combinations.push({ origin, destination });
            });
        });
        return { names: ['origin', 'destination'], combinations };
    }

    if (!fields.account) return null;

    // Onde o formulário pede cartão, o crédito só serve em conta que tenha um.
    const owners = new Set(Object.values(options.cards));
    const lacksCard = (account, method) => Boolean(card) && method === CREDIT && !owners.has(account);

    // Parcelamento e cartão não perguntam tipo nem método: servem as contas que
    // aceitam a combinação que eles fixam.
    if (fixed.account) {
        const combinations = accountsAccepting(options, fixed.account)
            .filter((account) => !lacksCard(account, fixed.account.method))
            .map((account) => ({ account }));
        return { names: ['account'], combinations };
    }

    const combinations = [];
    Object.entries(options.rules).forEach(([account, types]) => {
        Object.entries(types).forEach(([type, methods]) => {
            methods.forEach((method) => {
                if (lacksCard(account, method)) return;
                options.natures[method].forEach((nature) => {
                    const category = nature === REGULAR ? ANY_CATEGORY : '';
                    combinations.push({ account, type, method, nature, category });
                });
            });
        });
    });
    const names = ['account', 'type', 'method', 'nature', 'category'].filter((name) => fields[name]);
    return { names, combinations };
}

function linkFields(form, options) {
    const modal = form.closest('[data-modal]');
    const fixed = (modal && options.fixed[modal.dataset.modal]) || {};

    const fields = {};
    ['account', 'origin', 'destination', 'type', 'method', 'nature', 'category'].forEach((name) => {
        const select = form.querySelector(`select[name="${name}"]`);
        if (select) fields[name] = select;
    });
    const account = fields.account;
    const method = fields.method;
    const card = form.querySelector('select[name="card"]');

    const linked = combinationsFor(fields, card, fixed, options);
    if (!linked) return;
    const { names, combinations } = linked;

    // Cartão com transações não troca de conta nem de final: na edição dele a
    // conta fica só com a que ele já tem. O id em edição vem do CRUD da tela.
    const lastDigits = form.querySelector('[name="last_digits"]');
    const locked = () => Boolean(modal) && modal.dataset.modal === 'card' && options.locked_cards.includes(form.dataset.editing);

    // Valor vazio não recorta nada: é o campo que ainda não foi escolhido.
    function fits(combination, name) {
        const value = fields[name].value;
        return value === '' || combination[name] === ANY_CATEGORY || combination[name] === value;
    }

    // O que cabe no campo com o que está escolhido em todos os outros. Devolver
    // null é dizer "sem restrição", o caso da categoria em natureza Normal.
    //
    // A categoria recebe o recorte, mas não o impõe: uma categoria marcada não
    // esconde as outras naturezas, e escolher uma delas é que limpa a categoria.
    // Do contrário, a transação que já tem categoria não chegaria a Interna sem
    // o usuário adivinhar que precisa apagá-la antes.
    function allowedFor(name) {
        if (name === 'account' && locked()) return [account.value];

        const values = new Set(
            combinations
                .filter((combination) => names.every((other) => other === name || other === 'category' || fits(combination, other)))
                .map((combination) => combination[name]),
        );
        return values.has(ANY_CATEGORY) ? null : Array.from(values);
    }

    function cardsFor() {
        const accounts = account.value ? [account.value] : allowedFor('account');
        return Object.keys(options.cards).filter((id) => accounts.includes(options.cards[id]));
    }

    // Esconde e desabilita de uma vez: escondida, a opção sai da lista;
    // desabilitada, ela também deixa de ser alcançável pelo teclado e por
    // navegador que ignore o hidden.
    // Devolve se marcou sozinho a única opção de um campo que tinha a vazia.
    function restrict(select, allowed) {
        const usable = [];
        let blank = null;

        Array.from(select.options).forEach((option) => {
            // A opção vazia nunca é cortada: é por ela que se desfaz a escolha.
            if (option.value === '') {
                blank = option;
                return;
            }

            const fits = allowed === null || allowed.includes(option.value);
            option.hidden = !fits;
            option.disabled = !fits;
            if (fits) usable.push(option);
        });

        const chosen = select.selectedOptions[0];
        if (chosen && chosen.value !== '' && !chosen.disabled) return false;

        // Uma opção só não é escolha, é constatação — e onde não existe opção
        // vazia, como no cartão e na natureza, alguma precisa ficar marcada. Nos
        // demais casos a decisão volta para o usuário em vez de ser adivinhada.
        select.value = usable.length && (!blank || usable.length === 1) ? usable[0].value : '';
        return Boolean(blank) && select.value !== '';
    }

    // Cartão só existe no crédito: nos demais métodos o campo nem aparece.
    // Escondê-lo não basta — o select continuaria enviando a primeira opção, e
    // o servidor recusa cartão fora do crédito. O disabled é o que o tira do
    // POST; o navegador não envia campo desabilitado. Sem select de método, no
    // parcelamento, o cartão é sempre exigido.
    function showCard() {
        if (!card) return;

        const wrapper = card.closest('[data-field]');
        const credit = !method || method.value === CREDIT;

        if (wrapper) wrapper.hidden = !credit;
        card.disabled = !credit;
    }

    // Os campos que o recorte marcou sozinho. Esse valor não foi escolha do
    // usuário, então não pode seguir recortando os outros: a cada mudança ele
    // volta a vazio e só é marcado de novo se ainda for a única opção. Sem isso,
    // voltar a um campo mais aberto deixaria presos o tipo e o método que a
    // escolha anterior tinha imposto.
    const automatic = new Set();

    // Recortar um campo pode marcar sozinho a única opção que sobrou nele, e
    // isso muda o que cabe nos outros. Repete até nenhum valor mudar; como cada
    // marcação só estreita o que já estava escolhido, isso acaba em poucas voltas.
    function sync() {
        automatic.forEach((name) => {
            fields[name].value = '';
        });
        automatic.clear();

        const snapshot = () => names.map((name) => fields[name].value).join('|');

        for (let round = 0; round <= names.length; round += 1) {
            const before = snapshot();
            names.forEach((name) => {
                if (restrict(fields[name], allowedFor(name))) automatic.add(name);
            });
            if (snapshot() === before) break;
        }

        if (card) restrict(card, cardsFor());
        if (lastDigits) lastDigits.readOnly = locked();
        showCard();
    }

    // O que o usuário escolhe à mão deixa de ser marcação automática.
    names.forEach((name) => fields[name].addEventListener('change', () => {
        automatic.delete(name);
        sync();
    }));

    // O caminho inverso: o cartão pertence a uma conta só, então escolhê-lo já
    // responde qual é a conta. Perguntar de novo seria pedir duas vezes a mesma
    // informação, e a conta segue livre para ser trocada depois — o que troca,
    // aí, é o cartão.
    if (card) {
        card.addEventListener('change', () => {
            const owner = options.cards[card.value];
            if (owner && account) {
                account.value = owner;
                automatic.delete('account');
            }
            sync();
        });
    }

    // A edição preenche os campos de fora e a criação usa o reset; nos dois
    // casos o modal avisa por este evento, para as opções se refazerem a partir
    // dos valores novos.
    // Os valores que chegam por aqui são do registro, não do recorte.
    form.addEventListener(SYNC_OPTIONS, () => {
        automatic.clear();
        sync();
    });

    sync();
}

/* CRUD das listagens ------------------------------------------------------ */

// Transação e cartão são a mesma tela com nomes diferentes: uma lista, um modal
// que cria e edita, outro que confirma a remoção. O que muda entre elas — o
// nome do registro, os títulos e as rotas — vem do próprio template.
function setupRecordCrud(root) {
    const { kind, createTitle, updateTitle, createUrl, updateUrl, deleteUrl } = root.dataset;

    const modal = document.querySelector(`[data-modal="${kind}"]`);
    const deleteModal = document.querySelector(`[data-modal="${kind}-delete"]`);
    if (!modal || !deleteModal) return;

    const form = modal.querySelector('[data-modal-form]');
    const title = modal.querySelector('[data-modal-title]');
    const modalDelete = modal.querySelector('[data-modal-delete]');
    const deleteForm = deleteModal.querySelector('[data-delete-form]');
    const deleteLabel = deleteModal.querySelector('[data-delete-label]');
    const deleteWarning = deleteModal.querySelector('[data-delete-warning]');

    // Linha aberta no modal de edição, para o botão Excluir saber o alvo.
    let editingRow = null;

    // As rotas vêm com o pk 0 como molde, já que o {% url %} exige um valor.
    // O segmento é trocado inteiro: casar só "0/" pegaria qualquer zero da
    // URL, e ancorar no fim nunca casaria, já que o pk vem antes da ação.
    function urlFor(template, id) {
        return template.replace(/\/0\//, `/${id}/`);
    }

    // Os campos são preenchidos pelo próprio nome, e não um a um: o dataset
    // entrega data-last-digits como lastDigits, que é o name do campo em
    // camelCase. Assim um campo novo no formulário não exige mexer aqui.
    function fill(data) {
        form.querySelectorAll('[name]').forEach((input) => {
            const key = input.name.replace(/_(.)/g, (match, letter) => letter.toUpperCase());
            if (key in data) input.value = data[key];
        });
    }

    function openCreate() {
        form.action = createUrl;
        title.textContent = createTitle;
        form.reset();
        delete form.dataset.editing;
        editingRow = null;
        if (modalDelete) modalDelete.hidden = true;
        form.dispatchEvent(new Event(SYNC_OPTIONS));
        modal.showModal();
    }

    function openEdit(row) {
        form.action = urlFor(updateUrl, row.dataset.id);
        title.textContent = updateTitle;
        editingRow = row;
        form.dataset.editing = row.dataset.id;
        if (modalDelete) modalDelete.hidden = false;
        fill(row.dataset);
        form.dispatchEvent(new Event(SYNC_OPTIONS));
        modal.showModal();
    }

    function openDelete(row) {
        deleteForm.action = urlFor(deleteUrl, row.dataset.id);
        deleteLabel.textContent = row.dataset.label;

        // Linha derivada apaga o registro de origem inteiro, e com ele as
        // outras transações que vieram junto. Avisar antes é o que separa o
        // gesto pretendido da surpresa de ver a lista encolher. A frase vem
        // pronta do servidor, que sabe o gênero de cada origem.
        if (deleteWarning) {
            const warning = row.dataset.originWarning || '';
            deleteWarning.textContent = warning;
            deleteWarning.hidden = !warning;
        }

        deleteModal.showModal();
    }

    // Cancelar a confirmação não volta para a edição: o modal já foi fechado e
    // reabri-lo sozinho seria surpreendente.
    deleteModal.addEventListener('close', () => {
        editingRow = null;
    });

    // Excluir de dentro da edição: fecha este modal antes de abrir a
    // confirmação, porque dois <dialog> modais empilhados prendem o foco no
    // primeiro.
    if (modalDelete) {
        modalDelete.addEventListener('click', () => {
            if (!editingRow) return;
            const row = editingRow;
            modal.close();
            openDelete(row);
        });
    }

    setupNewButton(kind, openCreate);

    document.querySelectorAll(`[data-row="${kind}"]`).forEach((row) => {
        // Remover vale para toda linha removível, inclusive a derivada, que não
        // é editável mas apaga a própria origem. Por isso o listener fica no
        // botão, e não na linha: só a editável reage ao clique no corpo dela.
        const remove = row.querySelector('[data-row-delete]');
        if (remove) {
            remove.addEventListener('click', (event) => {
                // Nas linhas editáveis o clique também sobe para o handler da
                // linha, que abriria a edição por cima da confirmação.
                event.stopPropagation();
                openDelete(row);
            });
        }

        if (!row.hasAttribute('data-editable')) return;

        row.addEventListener('click', () => openEdit(row));

        // Teclado: a linha é focável, então Enter e Espaço abrem a edição.
        row.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openEdit(row);
            }
        });
    });
}

// Escolha do que lançar, entre o botão "Nova Transação" e o formulário. Cada
// opção abre o modal do seu tipo; o avulso não é aberto aqui porque precisa do
// reset e da action de criação que só o CRUD do registro conhece. Sem o seletor
// no DOM — a tela de cartões — o botão abre direto a criação.
function setupNewButton(kind, openCreate) {
    const button = document.querySelector(`[data-new="${kind}"]`);
    if (!button) return;

    const picker = document.querySelector('[data-kind-modal]');
    if (!picker) {
        button.addEventListener('click', openCreate);
        return;
    }

    button.addEventListener('click', () => picker.showModal());

    picker.querySelectorAll('[data-kind-choice]').forEach((choice) => {
        choice.addEventListener('click', () => {
            const chosen = choice.dataset.kindChoice;

            // Fecha antes de abrir o próximo: dois <dialog> modais empilhados
            // prendem o foco no primeiro, como já acontece na exclusão.
            picker.close();

            if (chosen === kind) {
                openCreate();
                return;
            }

            const modal = document.querySelector(`[data-modal="${chosen}"]`);
            if (!modal) return;
            const chosenForm = modal.querySelector('[data-modal-form]');
            chosenForm.reset();
            chosenForm.dispatchEvent(new Event(SYNC_OPTIONS));
            modal.showModal();
        });
    });
}

/* Charts ------------------------------------------------------------------ */

const CHART_PALETTE = [
    '#5aa469', '#3f4d7a', '#e6b422', '#c0504d', '#5b9bd5',
    '#8064a2', '#e8823c', '#4bc0a8', '#9fb63c', '#7f6084',
];

const MOBILE_BREAKPOINT = 768;

function isMobile() {
    return window.innerWidth <= MOBILE_BREAKPOINT;
}

// Só o número, sem símbolo: o sistema inteiro é em real, e repetir a moeda em
// cada linha, rótulo e tooltip não diz nada que a tela já não diga.
function formatCurrency(value) {
    return value.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatCompact(value) {
    // Valor curto para os rótulos sobre as barras, que ficam lado a lado.
    if (Math.abs(value) >= 1000) {
        return (value / 1000).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + 'k';
    }
    return value.toLocaleString('pt-BR', { maximumFractionDigits: 0 });
}

function buildChart(element, buildOption) {
    // Sem devicePixelRatio o ECharts desenha o canvas a 1x e o navegador
    // amplia, deixando texto e linhas borrados em tela HiDPI ou com zoom.
    function create() {
        const instance = echarts.init(element, null, {
            devicePixelRatio: window.devicePixelRatio || 1,
            renderer: 'canvas',
        });
        instance.setOption(buildOption());
        return instance;
    }

    let chart = create();
    let wasMobile = isMobile();
    let ratio = window.devicePixelRatio;

    window.addEventListener('resize', () => {
        // O zoom do navegador muda o devicePixelRatio, e o resize sozinho não
        // reamostra o canvas: só recriando o gráfico volta a ficar nítido.
        if (window.devicePixelRatio !== ratio) {
            ratio = window.devicePixelRatio;
            wasMobile = isMobile();
            chart.dispose();
            chart = create();
            return;
        }

        // Só reconstrói ao cruzar o breakpoint; senão apenas redimensiona.
        if (isMobile() !== wasMobile) {
            wasMobile = isMobile();
            chart.setOption(buildOption(), true);
        }
        chart.resize();
    });
}

// Cada série carrega o próprio rótulo e cor no JSON, para que a view decida o
// que exibir sem precisar de um ramo por template aqui.
function setupBarChart(element) {
    const data = readJsonScript(element.dataset.barChart);
    if (!data) return;

    buildChart(element, () => {
        const mobile = isMobile();

        return {
            color: data.series.map((item) => item.color),
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'shadow' },
                valueFormatter: formatCurrency,
            },
            legend: { show: data.series.length > 1, top: 0 },
            grid: {
                left: mobile ? 4 : 12,
                right: mobile ? 4 : 12,
                bottom: mobile ? 4 : 12,
                top: 48,
                containLabel: true,
            },
            xAxis: {
                type: 'category',
                data: data.labels,
                // interval 0 força todos os meses a aparecerem; sem isso o
                // ECharts pula rótulos e some com metade dos períodos.
                axisLabel: mobile
                    ? { interval: 0, rotate: 45, fontSize: 10 }
                    : { interval: 0, fontSize: 11 },
            },
            // Sem os valores na lateral: o número de cada barra já aparece
            // sobre ela e o tooltip mostra o valor exato.
            yAxis: {
                type: 'value',
                axisLabel: { show: false },
            },
            // No celular os rótulos sobre as barras viram poluição: o toque
            // abre o tooltip com o valor exato.
            series: data.series.map((item) => ({
                name: item.name,
                type: 'bar',
                data: item.data,
                label: {
                    show: !mobile,
                    position: 'top',
                    // Compacto porque com duas séries lado a lado o valor cheio
                    // de uma barra encostava no da vizinha.
                    formatter: (params) => formatCompact(params.value),
                    fontSize: 10,
                },
            })),
        };
    });
}

function setupDonutChart(element) {
    const data = readJsonScript(element.dataset.donutChart);
    if (!data) return;

    buildChart(element, () => {
        const mobile = isMobile();

        return {
            color: CHART_PALETTE,
            tooltip: { trigger: 'item', valueFormatter: formatCurrency },
            legend: mobile
                ? {
                      type: 'scroll',
                      orient: 'horizontal',
                      bottom: 0,
                      left: 'center',
                      itemWidth: 10,
                      itemHeight: 10,
                      textStyle: { fontSize: 11 },
                  }
                : {
                      type: 'scroll',
                      orient: 'vertical',
                      right: 8,
                      top: 'center',
                      itemWidth: 12,
                      itemHeight: 12,
                  },
            series: [{
                type: 'pie',
                radius: mobile ? ['40%', '62%'] : ['48%', '72%'],
                center: mobile ? ['50%', '42%'] : ['34%', '50%'],
                data: data,
                label: {
                    show: !mobile,
                    formatter: (params) => formatCurrency(params.value),
                    fontSize: 10,
                },
                // Evita que as fatias finas empilhem os rótulos umas sobre as
                // outras: o ECharts espaça os vizinhos ao longo da linha-guia.
                labelLayout: { hideOverlap: true },
                minAngle: 2,
            }],
        };
    });
}
