/* Chart styling only. Theme updates retain data and metric semantics. */
export function token(name, fallback = '#475569') {
  return typeof getComputedStyle === 'function' ? getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback : fallback;
}
export const chartColor = index => token(`--chart-${index}`);

function themeSeries(chart) {
  for (const series of chart.data.datasets) { if (series.gitvizColorIndex) { series.backgroundColor = chartColor(series.gitvizColorIndex); series.borderColor = chartColor(series.gitvizColorIndex); } }
  if (chart.canvas.id === 'chart-timeline' && !chart.data.datasets[0]?.gitvizColorIndex) {
    chart.data.datasets.forEach((series, index) => {
      if (index === 0) series.backgroundColor = chartColor(1);
      else series.borderColor = chartColor(index === 1 ? 2 : 4);
    });
  }
  if (chart.canvas.id === 'chart-donut') {
    const order = { '기능 추가': 1, '리팩토링': 4, '버그 수정': 2, '문서': 5, '유지 작업': 3, '기타': 6 };
    chart.data.datasets[0].backgroundColor = chart.data.labels.map(label => chartColor(order[label] || 6));
  }
}

if (typeof Chart !== 'undefined') {
  // Quieter, rounder defaults: soft bars, thin lines, short entrance animation. Data and axes are untouched.
  Chart.defaults.animation = { duration: 700, easing: 'easeOutQuart' };
  Chart.defaults.elements.bar.borderRadius = 4;
  Chart.defaults.elements.bar.borderSkipped = false;
  Chart.defaults.elements.line.borderWidth = 2.5;
  Chart.defaults.elements.point.hoverRadius = 5;
  Object.assign(Chart.defaults.plugins.tooltip, { padding: 10, cornerRadius: 8, boxPadding: 4, titleMarginBottom: 6 });
  Chart.register({
    id: 'gitvizTheme',
    beforeUpdate(chart) {
      chart.options.color = token('--ui-muted');
      chart.options.font = { ...chart.options.font, family: token('--ui-font', 'Segoe UI'), size: 12 };
      if (chart.options.plugins?.tooltip) {
        Object.assign(chart.options.plugins.tooltip, { backgroundColor: token('--ui-surface'), borderColor: token('--ui-border-strong'), titleColor: token('--ui-text'), bodyColor: token('--ui-text'), footerColor: token('--ui-text'), borderWidth: 1 });
      }
      const cartesian = ['bar', 'line'].includes(chart.config.type);
      for (const [axis, scale] of Object.entries(chart.options.scales || {})) {
        if (scale.ticks) scale.ticks.color = token('--ui-muted');
        if (scale.title) scale.title.color = token('--ui-muted');
        if (scale.grid) { scale.grid.color = token('--ui-border'); scale.grid.drawTicks = false; if (cartesian && axis.startsWith('x')) scale.grid.display = false; }
        if (cartesian) scale.border = { ...scale.border, display: false };
      }
      themeSeries(chart);
    },
  });
  window.addEventListener('gitviz-theme-changed', () => {
    for (const chart of Object.values(Chart.instances)) {
      // Apply before update resolves dataset options, so painted elements change too.
      // 'none' would leave Chart.js's cached shared element options (e.g. bar colours) at the old theme; 'resize' refreshes them without animating.
      themeSeries(chart); chart.update('resize');
    }
  });
}
