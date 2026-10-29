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
    const order = { '기능 추가': 1, '리팩토링': 4, '버그 픽스': 2, '문서': 5, '잡무': 3, '기타': 6 };
    chart.data.datasets[0].backgroundColor = chart.data.labels.map(label => chartColor(order[label] || 6));
  }
}

if (typeof Chart !== 'undefined') {
  Chart.register({
    id: 'gitvizTheme',
    beforeUpdate(chart) {
      chart.options.color = token('--ui-muted');
      chart.options.font = { ...chart.options.font, family: token('--ui-font', 'Segoe UI'), size: 12 };
      if (chart.options.plugins?.tooltip) {
        Object.assign(chart.options.plugins.tooltip, { backgroundColor: token('--ui-surface'), borderColor: token('--ui-border-strong'), titleColor: token('--ui-text'), bodyColor: token('--ui-text'), borderWidth: 1 });
      }
      for (const scale of Object.values(chart.options.scales || {})) {
        if (scale.ticks) scale.ticks.color = token('--ui-muted');
        if (scale.title) scale.title.color = token('--ui-muted');
        if (scale.grid) scale.grid.color = token('--ui-border');
      }
      themeSeries(chart);
    },
  });
  window.addEventListener('gitviz-theme-changed', () => {
    for (const chart of Object.values(Chart.instances)) {
      // Apply before update resolves dataset options, so painted elements change too.
      themeSeries(chart); chart.update('none');
    }
  });
}
