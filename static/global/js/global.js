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
});

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
