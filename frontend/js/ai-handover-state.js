/* ── 인수인계 화면의 순수 로직 (DOM·네트워크 없음, node에서 시험한다) ──
   - 서버가 판단한다: 여기의 검증은 입력을 일찍 알려 주는 안내일 뿐이고, 저장·완료 조건은 서버 응답 코드로 안내한다.
   - 미입력은 항상 "미확인"이다. 미확인을 "준비 필요"나 "준비 완료"로 바꾸지 않고, AI 지적·미확인 내용을 확정 사실로 표시하지 않는다. */

export const ITEM_KEYS = ['assignee', 'description', 'run_method', 'verify_method', 'joint_review', 'receiver_run'];
export const ITEM_LABEL = {
  assignee: '인수 담당자', description: '기능 설명', run_method: '실행 방법', verify_method: '수정 후 확인 절차', joint_review: '공동 검토', receiver_run: '인수자 실행 확인',
};
export const ITEM_HINT = {
  assignee: '인수할 사람을 정하고 이름을 기록합니다.',
  description: '기능과 인계 범위에 대한 설명 또는 문서 링크를 기록합니다.',
  run_method: '환경 준비와 실행 절차를 작성하거나 문서 링크를 기록합니다.',
  verify_method: '수정 후 확인할 방법과 기대 결과를 작성하거나 문서 링크를 기록합니다.',
  joint_review: '관련 파일을 함께 검토했다면 그 결과를 기록합니다.',
  receiver_run: '인수자가 직접 실행한 결과와 남은 질문을 기록합니다.',
};
export const STATUS_LABEL = { unknown: '미확인', needed: '준비 필요', ready: '준비 완료 (작성자 기록)' };
export const STATUS_OPTIONS = [['unknown', '미확인'], ['needed', '준비 필요'], ['ready', '준비 완료']];
export const READY_NOTE = '"준비 완료"는 작성자가 기록한 상태입니다. GitViz가 문서 내용이나 실행 성공을 확인한 것이 아닙니다.';
export const LINK_NOTE = '링크는 저장하고 표시만 합니다. GitViz가 링크를 열거나 내용을 읽지 않습니다.';
export const DRAFT_NOTE = '초안은 모델 호출 없이 저장된 자료를 정리한 것입니다. AI의 설명·지적은 사람이 검증하기 전에는 확정 사실이 아니며, 미확인 항목은 미확인으로 남습니다.';
export const MAX_PATHS = 50;
export const MAX_PATH_CHARS = 500;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (v) => typeof v === 'string' && UUID_RE.test(v);
const posInt = (v) => (/^[1-9][0-9]{0,9}$/.test(String(v ?? '')) ? Number(v) : null);

export function parseRoute(search) {
  const q = new URLSearchParams(search || '');
  const repo = posInt(q.get('repo'));
  const rawTarget = q.get('target');
  const invalid = [];
  if (q.get('repo') && repo === null) invalid.push('repo');
  if (rawTarget && !isUuid(rawTarget)) invalid.push('target');
  return { repo, target: isUuid(rawTarget) ? rawTarget.toLowerCase() : null, invalid };
}

export function buildRoute({ repo = null, target = null } = {}) {
  const p = new URLSearchParams();
  if (repo) p.set('repo', String(repo));
  if (repo && target) p.set('target', target);
  const s = p.toString();
  return s ? `?${s}` : '';
}

/* 줄마다 하나씩 적은 경로를 목록으로. 서버 규칙과 같은 기준으로 일찍 안내한다. */
export function parsePaths(text) {
  const errors = [];
  const paths = [];
  const seen = new Set();
  for (const raw of String(text || '').split('\n')) {
    let p = raw.trim().replaceAll('\\', '/');
    if (!p) continue;
    while (p.startsWith('./')) p = p.slice(2);
    if (p.length > MAX_PATH_CHARS || p.startsWith('/') || /[\u0000-\u001f\u007f]/.test(p) || p.split('/').some((s) => s === '' || s === '.' || s === '..')) {
      errors.push(`올바르지 않은 경로: ${raw.trim().slice(0, 80)}`);
      continue;
    }
    if (!seen.has(p)) { seen.add(p); paths.push(p); }
  }
  if (!paths.length && !errors.length) errors.push('파일 경로를 한 줄에 하나씩 입력해 주세요.');
  if (paths.length > MAX_PATHS) errors.push(`파일은 ${MAX_PATHS}개까지 지정할 수 있습니다.`);
  return { paths, errors };
}

const LINK_RE = /^https?:\/\/[^\s<>"`\\\u0000-\u001f\u007f]{1,490}$/i;

/* 항목 하나의 입력 안내. 서버도 같은 규칙으로 거절한다. */
export function itemProblem(key, item) {
  const status = item?.status || 'unknown';
  const text = (item?.text || '').trim();
  const link = (item?.link || '').trim();
  if (link && !LINK_RE.test(link)) return '링크는 http 또는 https 주소여야 합니다.';
  if (status === 'unknown' && (text || link)) return '내용이나 링크가 있으면 "준비 필요" 또는 "준비 완료"로 정해 주세요.';
  if (status === 'ready') {
    if (key === 'description' || key === 'run_method' || key === 'verify_method') { if (!text && !link) return '준비 완료로 기록하려면 설명이나 링크를 입력해 주세요.'; }
    else if (!text) return '준비 완료로 기록하려면 내용을 입력해 주세요.';
  }
  return null;
}

export function preparationProblems(items) {
  const out = {};
  for (const key of ITEM_KEYS) { const p = itemProblem(key, items[key]); if (p) out[key] = p; }
  return out;
}

/* 폼 상태 → 요청 본문. 여섯 항목을 모두 보내고, 입력하지 않은 항목은 unknown으로 둔다. */
export function buildPreparationBody({ expectedVersion, targetVersion, items }) {
  const out = {};
  for (const key of ITEM_KEYS) {
    const it = items[key] || {};
    const text = (it.text || '').trim();
    const link = (it.link || '').trim();
    out[key] = { status: it.status || 'unknown', text: text || null, link: link || null };
  }
  return { expected_version: expectedVersion, confirmed_target_version: targetVersion, items: out };
}

export function itemsFromView(view) {
  const out = {};
  for (const it of view?.items || []) out[it.key] = { status: it.status, text: it.text || '', link: it.link || '' };
  for (const key of ITEM_KEYS) out[key] = out[key] || { status: 'unknown', text: '', link: '' };
  return out;
}

export function preparationSummary(view) {
  const counts = { unknown: 0, needed: 0, ready: 0 };
  for (const it of view?.items || []) counts[it.status] = (counts[it.status] || 0) + 1;
  return `미확인 ${counts.unknown} · 준비 필요 ${counts.needed} · 준비 완료(작성자 기록) ${counts.ready}`;
}

export const STALE_TEXT = {
  target_changed: '인계 대상 정보가 이 초안을 만든 뒤 바뀌었습니다.',
  preparation_changed: '준비 정보가 이 초안을 만든 뒤 바뀌었습니다.',
  feedback_changed: 'AI 지적에 대한 피드백이 이 초안을 만든 뒤 바뀌었습니다.',
  source_missing: '이 초안에 쓴 AI 결과를 더 이상 읽을 수 없습니다(삭제 또는 보관 기간 만료).',
};
export function staleLines(reasons) { return (reasons || []).map((r) => STALE_TEXT[r] || `기준이 바뀌었습니다(${r}).`); }

const ISSUE_TEXT = {
  not_text: '문서는 텍스트여야 합니다.', too_long: '문서가 너무 깁니다(최대 100,000자).', control_chars: '제어 문자가 포함돼 있습니다.',
  hidden_or_bidi_chars: '보이지 않는 문자나 방향 제어 문자가 포함돼 있습니다.', line_too_long: '한 줄이 너무 깁니다(최대 4,000자).',
  raw_html: 'HTML 태그는 쓸 수 없습니다(코드 블록·코드 span 안은 가능합니다).', image: '이미지는 넣을 수 없습니다.',
  unsafe_link: '링크는 http, https, mailto 또는 같은 문서·상대 경로만 쓸 수 있습니다.',
};

/* 인수인계 API 오류 → 안내. 버전 충돌의 이유별 문구를 덧붙이고, 나머지는 호출하는 쪽의 공통 안내를 쓴다. */
export function describeHandoverError(err) {
  const code = err?.code || null;
  const reason = err?.details?.reason || null;
  const out = (title, body, action = 'reload_target') => ({ title, body, action, tone: 'warn', specific: true });
  if (code === 'VERSION_CONFLICT') {
    switch (reason) {
      case 'TARGET_CHANGED': return out('인계 대상이 바뀌었습니다', '대상 정보를 다시 불러왔습니다. 확인한 뒤 다시 저장하거나 생성해 주세요.');
      case 'TARGET_VERSION': return out('다른 곳에서 인계 대상이 바뀌었습니다', '최신 내용을 다시 불러왔습니다. 확인한 뒤 다시 시도해 주세요.');
      case 'PREPARATION_VERSION': return out('다른 곳에서 준비 정보가 바뀌었습니다', '최신 내용을 다시 불러왔습니다. 내 입력과 비교한 뒤 다시 저장해 주세요.');
      case 'PREPARATION_CHANGED': return out('준비 정보가 바뀌었습니다', '최신 준비 정보를 불러왔습니다. 다시 생성해 주세요.');
      case 'DOCUMENT_VERSION': return out('다른 곳에서 문서가 바뀌었습니다', '최신 문서를 불러왔습니다. 내가 쓴 내용은 아래 입력란에 남겨 두었으니 비교한 뒤 다시 저장해 주세요.', 'reload_document');
      default: return out('다른 곳에서 먼저 바뀌었습니다', '최신 내용을 불러왔습니다. 확인한 뒤 다시 시도해 주세요.');
    }
  }
  if (code === 'INVALID_SELECTION' && Array.isArray(err?.details?.issues) && err.details.issues.length) {
    return { title: '문서에 저장할 수 없는 내용이 있습니다', body: err.details.issues.map((i) => ISSUE_TEXT[i] || i).join(' '), action: null, tone: 'error', specific: true };
  }
  if (code === 'INVALID_SELECTION' && err?.details?.item) {
    return { title: '준비 항목을 확인해 주세요', body: String(err.message || '').slice(0, 300), action: null, tone: 'error', specific: true };
  }
  if (code === 'INVALID_STATE' && Array.isArray(err?.details?.issues)) {
    return { title: '내보낼 수 없는 내용이 있습니다', body: err.details.issues.map((i) => ISSUE_TEXT[i] || i).join(' ') + ' 문서를 고친 뒤 다시 시도해 주세요.', action: null, tone: 'error', specific: true };
  }
  return null;
}

export function resultOptions(jobs, kind) {
  return (jobs || []).filter((j) => j && j.kind === kind && j.status === 'succeeded' && isUuid(j.result_id))
    .map((j) => ({ resultId: j.result_id, label: `${kind === 'review' ? '코드 리뷰' : '변경 설명'} · ${j.finished_at || j.created_at || ''}`.trim() }));
}

export const exportUrl = (documentId) => (isUuid(documentId) ? `/api/ai/v1/handover-documents/${encodeURIComponent(documentId)}/export?format=markdown` : null);
