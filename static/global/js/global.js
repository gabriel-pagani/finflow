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

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-multiselect]').forEach(setupMultiselect);
    bootstrapCharts();
    bootstrapTransactionCrud();
});

/* Bootstrap --------------------------------------------------------------- */

// A inicialização é dirigida por data-attributes em vez de <script> inline
// para que a CSP possa recusar script inline sem 'unsafe-inline'.

function readJsonScript(id) {
    const node = document.getElementById(id);
    return node ? JSON.parse(node.textContent) : null;
}

function bootstrapCharts() {
    document.querySelectorAll('[data-bar-chart]').forEach((element) => {
        const data = readJsonScript(element.dataset.barChart);
        if (!data) return;

        // Cada série carrega o próprio rótulo e cor no JSON, para que a view
        // decida o que exibir sem precisar de um ramo por template aqui.
        renderBarChart(element.id, { labels: data.labels, series: data.series });
    });

    document.querySelectorAll('[data-donut-chart]').forEach((element) => {
        const data = readJsonScript(element.dataset.donutChart);
        if (!data) return;

        renderDonutChart(element.id, data);
    });
}

function bootstrapTransactionCrud() {
    const root = document.querySelector('[data-transaction-urls]');
    if (!root) return;

    setupTransactionCrud({
        createUrl: root.dataset.createUrl,
        updateUrl: root.dataset.updateUrl,
        deleteUrl: root.dataset.deleteUrl,
    });
}

/* Kind picker ------------------------------------------------------------- */

// Escolha do que lançar, entre o botão "Nova Transação" e o formulário. Cada
// opção abre o modal do seu tipo; o avulso não é aberto aqui porque precisa do
// reset e da action de criação que só o CRUD de transação conhece.
function setupKindPicker(openTransactionCreate) {
    const newButton = document.querySelector('[data-transaction-new]');
    if (!newButton) return;

    const picker = document.querySelector('[data-kind-modal]');
    if (!picker) {
        newButton.addEventListener('click', openTransactionCreate);
        return;
    }

    newButton.addEventListener('click', () => picker.showModal());

    picker.querySelectorAll('[data-kind-choice]').forEach((button) => {
        button.addEventListener('click', () => {
            const kind = button.dataset.kindChoice;

            // Fecha antes de abrir o próximo: dois <dialog> modais empilhados
            // prendem o foco no primeiro, como já acontece na exclusão.
            picker.close();

            if (kind === 'transaction') {
                openTransactionCreate();
                return;
            }

            const modal = document.querySelector(`[data-modal="${kind}"]`);
            if (!modal) return;
            modal.querySelector('[data-modal-form]').reset();
            modal.showModal();
        });
    });
}

/* Transaction CRUD -------------------------------------------------------- */

function setupTransactionCrud(urls) {
    const modal = document.querySelector('[data-modal="transaction"]');
    const deleteModal = document.querySelector('[data-delete-modal]');
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
        return template.replace(/\/0\//, '/' + id + '/');
    }

    function field(name) {
        return form.querySelector(`[name="${name}"]`);
    }

    function openCreate() {
        form.action = urls.createUrl;
        title.textContent = 'Nova Transação';
        form.reset();
        editingRow = null;
        if (modalDelete) modalDelete.hidden = true;
        modal.showModal();
    }

    function openEdit(row) {
        const data = row.dataset;
        form.action = urlFor(urls.updateUrl, data.id);
        title.textContent = 'Editar Transação';
        editingRow = row;
        if (modalDelete) modalDelete.hidden = false;

        field('datetime').value = data.datetime;
        field('account').value = data.account;
        field('type').value = data.type;
        field('method').value = data.method;
        field('nature').value = data.nature;
        field('category').value = data.category;
        field('description').value = data.description;
        field('value').value = data.value;

        modal.showModal();
    }

    function openDelete(row) {
        deleteForm.action = urlFor(urls.deleteUrl, row.dataset.id);
        deleteLabel.textContent = row.dataset.label;

        // Linha derivada apaga o registro de origem inteiro, e com ele as
        // outras transações que vieram junto. Avisar antes é o que separa o
        // gesto pretendido da surpresa de ver a lista encolher. A frase vem
        // pronta do servidor, que sabe o gênero de cada origem.
        if (deleteWarning) {
            const warning = row.dataset.originWarning;
            deleteWarning.textContent = warning || '';
            deleteWarning.hidden = !warning;
        }

        deleteModal.showModal();
    }

    // Cancelar a confirmação não volta para a edição: o modal já foi fechado e
    // reabri-lo sozinho seria surpreendente.
    deleteModal.addEventListener('close', () => {
        editingRow = null;
    });

    // "Nova Transação" abre a escolha do tipo; o formulário avulso é só uma das
    // saídas dela. Sem o seletor no DOM o botão volta a abrir direto o avulso.
    setupKindPicker(openCreate);

    // Excluir dentro do formulário de edição: fecha este modal e cai na mesma
    // confirmação usada pelo botão da linha. Empilhar dois <dialog> modais
    // prenderia o foco no primeiro, então a troca é sequencial.
    if (modalDelete) {
        modalDelete.addEventListener('click', () => {
            if (!editingRow) return;
            const row = editingRow;
            modal.close();
            openDelete(row);
        });
    }

    document.querySelectorAll('[data-modal-close]').forEach((button) => {
        button.addEventListener('click', () => button.closest('dialog').close());
    });

    // Clicar no fundo escuro fecha, comportamento que o <dialog> não dá de
    // graça. Vale para todo modal da página, inclusive os de parcelamento e
    // transferência, que não têm JS próprio além disto.
    document.querySelectorAll('dialog.modal').forEach((dialog) => {
        dialog.addEventListener('click', (event) => {
            if (event.target === dialog) dialog.close();
        });
    });

    // Remover vale para toda linha removível, inclusive a derivada, que não é
    // editável mas apaga a própria origem. Por isso o listener fica no botão, e
    // não na linha: só a avulsa reage ao clique no corpo dela.
    document.querySelectorAll('[data-transaction-row] [data-transaction-delete]').forEach((button) => {
        button.addEventListener('click', (event) => {
            // Nas linhas editáveis o clique também sobe para o handler da
            // linha, que abriria a edição por cima da confirmação.
            event.stopPropagation();
            openDelete(button.closest('[data-transaction-row]'));
        });
    });

    document.querySelectorAll('[data-transaction]').forEach((row) => {
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

/* Charts ------------------------------------------------------------------ */

const CHART_PALETTE = [
    '#5aa469', '#3f4d7a', '#e6b422', '#c0504d', '#5b9bd5',
    '#8064a2', '#e8823c', '#4bc0a8', '#9fb63c', '#7f6084',
];

const MOBILE_BREAKPOINT = 768;

function isMobile() {
    return window.innerWidth <= MOBILE_BREAKPOINT;
}

function formatCurrency(value) {
    return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatCompact(value) {
    // Valor curto para os rótulos sobre as barras, que ficam lado a lado.
    if (Math.abs(value) >= 1000) {
        return 'R$ ' + (value / 1000).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + 'k';
    }
    return 'R$ ' + value.toLocaleString('pt-BR', { maximumFractionDigits: 0 });
}

function buildChart(elementId, buildOption) {
    const element = document.getElementById(elementId);
    if (!element) return;

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

    return chart;
}

function renderBarChart(elementId, { labels, series }) {
    buildChart(elementId, () => {
        const mobile = isMobile();

        return {
            color: series.map((item) => item.color),
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'shadow' },
                valueFormatter: formatCurrency,
            },
            legend: { show: series.length > 1, top: 0 },
            grid: {
                left: mobile ? 4 : 12,
                right: mobile ? 4 : 12,
                bottom: mobile ? 4 : 12,
                top: 48,
                containLabel: true,
            },
            xAxis: {
                type: 'category',
                data: labels,
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
            series: series.map((item) => ({
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

function renderDonutChart(elementId, data) {
    buildChart(elementId, () => {
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
