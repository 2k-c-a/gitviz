/** Render repository strings exclusively as text. A path is never markup or a URL. */
export function statusError(code) {
  return code === 'CHANGESET_TOO_LARGE' ? '변경 파일 또는 저장소 항목이 너무 많아 조회할 수 없습니다. 커밋·stash로 변경 수를 줄이거나 더 작은 저장소 범위를 선택해 주세요.' : code;
}
export function renderStatus(target, state) {
  target.replaceChildren();
  const heading = document.createElement('p');
  heading.textContent = state.unborn ? `초기 커밋 없음 · ${state.branch ?? ''}` : state.detached ? `Detached HEAD · ${state.head}` : `${state.branch} · ${state.head}`;
  target.append(heading);
  for (const [key, label] of [['staged', 'Staged'], ['unstaged', 'Unstaged'], ['untracked', 'Untracked'], ['conflicts', 'Conflicts'], ['unsupported', '지원하지 않는 경로']]) {
    if (!state[key]) continue;
    const title = document.createElement('h3'); title.textContent = `${label} (${state[key].length})`; target.append(title);
    const list = document.createElement('ul');
    for (const item of state[key]) { const row = document.createElement('li'); row.textContent = `${item.status} ${item.oldPath ? `${item.oldPath} → ` : ''}${item.path}${item.unsupported ? ' · 링크·junction: 내용을 읽지 않았습니다.' : ''}`; list.append(row); }
    target.append(list);
  }
  const title = document.createElement('h3'); title.textContent = '최근 커밋'; target.append(title);
  const log = document.createElement('ol');
  for (const commit of state.recentCommits) { const row = document.createElement('li'); row.textContent = `${commit.oid.slice(0, 12)} ${commit.subject} · ${commit.author} · ${commit.authoredAt}`; log.append(row); }
  target.append(log);
  const reasons = {
    'submodule-status-not-expanded': 'submodule 내부 상태는 펼치지 않습니다.',
    'reparse-points-rejected': '링크·junction이 있는 저장소는 접근을 차단합니다.',
    'reparse-points-listed-unsupported': '링크·junction 항목은 내용을 읽지 않고 별도 미지원 목록으로 표시합니다. -- 표시는 Git 변경 상태를 판정하지 않았다는 뜻입니다.',
    'config-includes-rejected': '외부 Git 설정을 포함하는 저장소는 지원하지 않습니다.',
    'repository-filters-disabled': '저장소 필터를 실행하지 않아 필터가 적용된 파일의 상태는 일반 Git 조회와 다를 수 있습니다.',
    'sparse-checkout-metadata-present': 'sparse checkout 메타데이터가 있어 전체 파일이 로컬에 없을 수 있습니다.',
  };
  const limitations = document.createElement('p'); limitations.textContent = state.limitations.map(code => reasons[code] ?? code).join(' '); target.append(limitations);
}
