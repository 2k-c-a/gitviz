import { getRepoId, fetchSnapshotMeta, fetchRepoTree, fetchNetwork, fetchNetworkTopPairs } from './api.js';
import { renderLoadError } from './page-context.js';
import { element } from './ui/components.js';
import { loadPanel, action, valueText, empty } from './ui/analytics-view.js';
import { pageUrl } from './ui/shell.js';

// The activity panels (metrics, timeline, heatmap, contributors, releases, commit types) on this page belong to timeline.js.
if (document.getElementById('page-overview')) void init().catch(renderLoadError);
async function init() {
  // 'AI 분석' keeps this page's repository and stored result (?repo=&snapshot=).
  const insights = document.getElementById('btn-ai-insights'); if (insights) insights.href = pageUrl('ai-insights');
  const id = getRepoId(); await fetchSnapshotMeta(id);
  void loadPanel(document.getElementById('overview-files'), () => fetchRepoTree(id), (data, host) => {
    const rows = [...(data.files || [])].filter(row => row.observation_count > 0).sort((a, b) => b.observation_count - a.observation_count || a.path.localeCompare(b.path)).slice(0, 5);
    if (!rows.length) return empty(host, '최근 180일에 변경이 기록된 파일이 없습니다.');
    rows.forEach(row => { const item = element('div', 'analysis-list-row'); item.append(action(row.path, () => window.openFileDrilldown(row.file_id, row), 'ui-list-link'), element('span', '', valueText(row.observation_count, '회'))); host.append(item); });
    const link = element('a', '', '파일 분석에서 모두 보기'); link.href = pageUrl('code-analysis'); host.append(link);
  });
  void loadPanel(document.getElementById('overview-collaboration'), () => Promise.all([fetchNetwork(id), fetchNetworkTopPairs(id, 3)]), ([network, pairs], host) => {
    host.append(element('p', '', `전체 기여자 ${valueText(network.metrics?.total_contributor_count, '명')} · 협업 연결 ${valueText(network.metrics?.total_edge_count, '개')}`));
    if (!pairs.length) host.append(element('p', 'p-note', '관측 협업 쌍이 없습니다. PR·리뷰 수집 범위가 부족한 경우도 포함합니다.'));
    pairs.forEach(pair => host.append(action(`${pair.contributor_a.name} ↔ ${pair.contributor_b.name} · 강도 ${valueText(pair.edge_weight)}`, () => { location.href = pageUrl('network'); })));
    const link = element('a', '', '협업 네트워크 열기'); link.href = pageUrl('network'); host.append(link);
  });
}
