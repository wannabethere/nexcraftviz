const THEMES = ['nexcraftviz-light', 'nexcraftviz-dark'];
let current = 'nexcraftviz-light';

const bar = document.getElementById('themes');
if (bar) {
  THEMES.forEach(name => {
    const button = document.createElement('button');
    button.textContent = name.replace('nexcraftviz-', '');
    button.setAttribute('aria-pressed', String(name === current));
    button.onclick = () => {
      current = name;
      document.documentElement.setAttribute(
        'data-nxv-theme', name.endsWith('dark') ? 'dark' : 'light');
      [...bar.children].forEach((b, i) =>
        b.setAttribute('aria-pressed', String(THEMES[i] === name)));
      mountCharts();
    };
    bar.appendChild(button);
  });
}
document.documentElement.setAttribute('data-nxv-theme', 'light');

/** Build a Vega config from the CSS custom properties currently in force. */
function vegaConfig() {
  const style = getComputedStyle(document.documentElement);
  const read = name => style.getPropertyValue(name).trim();
  const count = parseInt(read('--nxv-cat-count') || '0', 10);
  const categorical = Array.from({length: count}, (_, i) => read(`--nxv-cat-${i + 1}`));
  return {
    background: 'transparent',
    font: read('--nxv-font'),
    view: {stroke: 'transparent'},
    range: {category: categorical},
    axis: {
      labelColor: read('--nxv-text-secondary'),
      titleColor: read('--nxv-text-secondary'),
      gridColor: read('--nxv-grid'),
      domainColor: read('--nxv-border'),
      tickColor: read('--nxv-border'),
    },
    axisBand: {grid: false, domain: false, ticks: false},
    legend: {
      labelColor: read('--nxv-text-secondary'),
      titleColor: read('--nxv-text-secondary'),
      symbolType: 'circle',
    },
    title: {color: read('--nxv-text'), subtitleColor: read('--nxv-text-secondary')},
    bar: {fill: categorical[0], cornerRadiusEnd: 3},
    line: {stroke: categorical[0], strokeWidth: 2},
    point: {fill: categorical[0], filled: true},
    arc: {fill: categorical[0]},
    rect: {fill: categorical[0]},
    text: {fill: read('--nxv-text')},
  };
}

function mountCharts() {
  const specs = window.__nxvSpecs || {};
  const config = vegaConfig();
  Object.entries(specs).forEach(([id, spec]) => {
    const el = document.getElementById(id);
    if (!el) return;
    // A spec that already carries a config chose it deliberately (the themed
    // panel in the walkthrough); leave that one alone.
    const merged = spec.config ? spec : {...spec, config};
    vegaEmbed(el, merged, {actions: false}).catch(err => {
      el.innerHTML = '<p class="nxv-warn">Chart failed to render: ' + err.message + '</p>';
    });
  });
}

/** Filter the gallery by chart type. */
const filters = document.querySelector('.nxv-filters');
if (filters) {
  filters.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    const wanted = button.dataset.filter;
    [...filters.children].forEach(b =>
      b.setAttribute('aria-pressed', String(b === button)));
    document.querySelectorAll('.nxv-gallery__item').forEach(item => {
      item.hidden = Boolean(wanted) && item.dataset.chartType !== wanted;
    });
  });
}

mountCharts();
