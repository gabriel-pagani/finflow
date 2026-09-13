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
    const boxes = Array.from(root.querySelectorAll('input[type="checkbox"]'));

    function updateLabel() {
        const checked = boxes.filter((box) => box.checked);

        if (checked.length === 0) {
            label.textContent = empty;
        } else if (checked.length === 1) {
            label.textContent = checked[0].nextElementSibling.textContent.trim();
        } else {
            // 'a' para Conta/Categoria, 'o' para Tipo/Método
            const suffix = root.dataset.gender === 'f' ? 'as' : 'os';
            label.textContent = `${checked.length} selecionad${suffix}`;
        }

        root.classList.toggle('has-selection', checked.length > 0);
    }

    boxes.forEach((box) => box.addEventListener('change', updateLabel));

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
// conta aceita vem das regras de negócio dela, e um cartão pertence a uma conta
// só. Isso poupa o usuário de montar um lançamento impossível e só descobrir no
// envio; quem valida de verdade continua sendo o model.
const SYNC_OPTIONS = 'finflow:sync-options';

// Único método que tem cartão; nos outros o campo nem aparece.
const CREDIT = 'CREDIT';

function setupLinkedFields() {
    const options = readJsonScript('data-form-options');
    if (!options) return;

    document.querySelectorAll('[data-modal-form]').forEach((form) => linkFields(form, options));
}

function linkFields(form, options) {
    const modal = form.closest('[data-modal]');
    const fixed = (modal && options.fixed[modal.dataset.modal]) || {};

    const account = form.querySelector('[name="account"]');
    const type = form.querySelector('[name="type"]');
    const method = form.querySelector('[name="method"]');
    const card = form.querySelector('[name="card"]');

    // A conta é o começo de tudo o que se recorta aqui. A transferência não
    // tem uma: as duas pernas dela já nascem com tipo e método fixos, e não há
    // o que escolher.
    if (!account) return;

    // Devolver null é dizer "sem restrição": ou a conta ainda não foi
    // escolhida, ou o formulário não fixa combinação nenhuma.
    function accountsFor() {
        if (!fixed.type) return null;
        return Object.keys(options.rules).filter((id) => (options.rules[id][fixed.type] || []).includes(fixed.method));
    }

    function typesFor() {
        const accepted = options.rules[account.value];
        return accepted ? Object.keys(accepted) : null;
    }

    function methodsFor() {
        const accepted = options.rules[account.value];
        if (!accepted) return null;
        // Sem tipo escolhido vale o que a conta aceita em qualquer um deles.
        return type.value ? (accepted[type.value] || []) : Object.values(accepted).flat();
    }

    function cardsFor() {
        return account.value ? Object.keys(options.cards).filter((id) => options.cards[id] === account.value) : null;
    }

    // Esconde e desabilita de uma vez: escondida, a opção sai da lista;
    // desabilitada, ela também deixa de ser alcançável pelo teclado e por
    // navegador que ignore o hidden.
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
        if (chosen && chosen.value !== '' && !chosen.disabled) return;

        // Uma opção só não é escolha, é constatação — e onde não existe opção
        // vazia, como no cartão, alguma precisa ficar marcada. Nos demais casos
        // a decisão volta para o usuário em vez de ser adivinhada.
        select.value = usable.length && (!blank || usable.length === 1) ? usable[0].value : '';
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

    // De cima para baixo: cada campo é recortado pelo que ficou acima dele, e
    // por isso o de baixo só é calculado depois que o de cima já se acertou.
    function sync() {
        if (account) restrict(account, accountsFor());
        if (type) restrict(type, typesFor());
        if (method) restrict(method, methodsFor());
        if (card) restrict(card, cardsFor());
        showCard();
    }

    [account, type, method].forEach((select) => {
        if (select) select.addEventListener('change', sync);
    });

    // O caminho inverso: o cartão pertence a uma conta só, então escolhê-lo já
    // responde qual é a conta. Perguntar de novo seria pedir duas vezes a mesma
    // informação, e a conta segue livre para ser trocada depois — o que troca,
    // aí, é o cartão.
    if (card) {
        card.addEventListener('change', () => {
            const owner = options.cards[card.value];
            if (owner && account) account.value = owner;
            sync();
        });
    }

    // A edição preenche os campos de fora e a criação usa o reset; nos dois
    // casos o modal avisa por este evento, para as opções se refazerem a partir
    // dos valores novos.
    form.addEventListener(SYNC_OPTIONS, sync);

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
        editingRow = null;
        if (modalDelete) modalDelete.hidden = true;
        form.dispatchEvent(new Event(SYNC_OPTIONS));
        modal.showModal();
    }

    function openEdit(row) {
        form.action = urlFor(updateUrl, row.dataset.id);
        title.textContent = updateTitle;
        editingRow = row;
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
