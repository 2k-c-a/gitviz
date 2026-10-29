import { getRepoId, fetchSnapshotMeta, fetchSummary, fetchTimeline, fetchTopContributors, fetchCommitTypes, fetchRepoTree, fetchNetwork, fetchNetworkTopPairs } from './api.js';
import { renderLoadError } from './page-context.js';
import { element } from './ui/components.js';
import { loadPanel, action, chart, contributorList, typeBars, valueText, empty } from './ui/analytics-view.js';
import { pageUrl } from './ui/shell.js';

if (document.getElementById('page-overview')) void init().catch(renderLoadError);
async function init() {
  const id = getRepoId(); await fetchSnapshotMeta(id);
  document.getElementById('overview-activity-link').href = pageUrl('timeline');
  void loadPanel(document.getElementById('overview-kpis'), () => fetchSummary(id), (data, host) => {
    for (const [label, value, unit, note] of [['관측 구간 커밋', data.total_commits, '개', '고정 관측 기간'], ['활성 기여자', data.active_contributors_90d, '명', '종료 기준 최근 90일'], ['릴리즈', data.total_releases, '개', '관측 구간 기준'], ['버그 수정 분류 비율', data.bugfix_pct, '%', 'bugfix 분류 / 관측 커밋']]) {
      const card = element('div', 'metric-card'); card.append(element('div', 'm-label', label), element('div', 'm-val', valueText(value, unit)), element('div', 'm-sub', note)); host.append(card);
    }
  });
  void loadPanel(document.getElementById('overview-trend'), () => fetchTimeline(id, 0), (data, host) => {
    if (!data.months?.length) return empty(host, '관측 구간에 커밋이 없습니다.');
    const frame = element('div', 'activity-chart'); const canvas = element('canvas'); canvas.setAttribute('aria-label', '월별 커밋 수'); frame.append(canvas); host.append(frame);
    chart(canvas, { labels: data.months, values: data.commits, label: '커밋 수' });
    const table = element('div', 'analysis-list'); data.months.forEach((month, i) => table.append(element('span', 'badge badge-gray', `${month}: ${valueText(data.commits[i], '건')}`))); host.append(table);
  });
  void loadPanel(document.getElementById('overview-contributors'), () => fetchTopContributors(id, 5), (data, host) => contributorList(host, data.contributors || []));
  void loadPanel(document.getElementById('overview-types'), () => fetchCommitTypes(id), (data, host) => typeBars(host, data.types || []));
  void loadPanel(document.getElementById('overview-files'), () => fetchRepoTree(id), (data, host) => {
    const rows = [...(data.files || [])].filter(row => row.observation_count > 0).sort((a, b) => b.observation_count - a.observation_count || a.path.localeCompare(b.path)).slice(0, 5);
    if (!rows.length) return empty(host, '관측 구간에 변경이 기록된 파일이 없습니다.');
    rows.forEach(row => { const item = element('div', 'analysis-list-row'); item.append(action(row.path, () => window.openFileDrilldown(row.file_id, row), 'ui-list-link'), element('span', '', valueText(row.observation_count, '회'))); host.append(item); });
    const link = element('a', '', '파일 분석에서 모두 보기'); link.href = pageUrl('code-analysis'); host.append(link);
  });
  void loadPanel(document.getElementById('overview-collaboration'), () => Promise.all([fetchNetwork(id), fetchNetworkTopPairs(id, 3)]), ([network, pairs], host) => {
    host.append(element('p', '', `전체 기여자 ${valueText(network.metrics?.total_contributor_count, '명')} · 협업 엣지 ${valueText(network.metrics?.total_edge_count, '개')}`));
    if (!pairs.length) host.append(element('p', 'p-note', '관측 협업 쌍이 없습니다. PR·리뷰 수집 범위가 부족한 경우도 포함합니다.'));
    pairs.forEach(pair => host.append(action(`${pair.contributor_a.name} ↔ ${pair.contributor_b.name} · 강도 ${valueText(pair.edge_weight)}`, () => { location.href = pageUrl('network'); })));
    const link = element('a', '', '협업 네트워크 열기'); link.href = pageUrl('network'); host.append(link);
  });
}
