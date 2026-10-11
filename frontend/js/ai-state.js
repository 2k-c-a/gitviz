/* ── AI 리뷰 화면의 순수 상태·문구 규칙 (DOM·fetch 의존 없음, node 테스트 가능) ──
   - 주소(?repo=&pr=&input=&job=&retry=)가 유일한 기준이다. 저장된 값·기본 저장소는 쓰지 않는다.
   - 서버 응답을 화면 문구로 바꾸는 규칙을 한 곳에 둔다. 지적 0건·사람 미검증·미실행을 "오류 없음"으로 말하지 않는다. */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const toInt = (v) => {
  const s = String(v ?? '').trim();
  const n = /^\d+$/.test(s) ? parseInt(s, 10) : NaN;
  return Number.isSafeInteger(n) && n > 0 ? n : null;
};
const toUuid = (v) => {
  const s = String(v ?? '').trim();
  return UUID_RE.test(s) ? s.toLowerCase() : null;
};

/* ── 주소 ── */
export const VIEWS = { NO_REPO: 'no_repo', HOME: 'home', FILES: 'files', PREVIEW: 'preview', JOB: 'job' };

export function parseRoute(search) {
  const p = new URLSearchParams(search || '');
  const invalid = [];
  const read = (name, conv) => {
    const raw = p.get(name);
    if (raw == null || raw === '') return null;
    const v = conv(raw);
    if (v == null) invalid.push(name);
    return v;
  };
  const repo = read('repo', toInt);
  if (repo == null) {
    /* repo 없이 온 pr/input/job은 어느 저장소의 것인지 알 수 없다 — 무시한다 */
    return { repo: null, pr: null, input: null, job: null, retry: null, force: false, view: VIEWS.NO_REPO, invalid };
  }
  const pr = read('pr', toInt);
  const commit = read('commit', (v) => /^[0-9a-f]{40}$/.test(v) ? v : null);
  if (pr && commit) invalid.push('commit');
  const input = read('input', toUuid);
  const job = read('job', toUuid);
  const retry = input ? read('retry', toUuid) : null;
  const force = !!input && !retry && p.get('force') === '1';          // 이미 결과가 있어도 새 generation으로 실행
  const view = job ? VIEWS.JOB : input ? VIEWS.PREVIEW : pr || commit ? VIEWS.FILES : VIEWS.HOME;
  return { repo, pr, commit: pr ? null : commit, input, job, retry, force, view, invalid };
}

export function buildRoute({ repo = null, pr = null, commit = null, input = null, job = null, retry = null, force = false } = {}) {
  if (repo == null) return '';
  const p = new URLSearchParams();
  p.set('repo', String(repo));
  if (job) p.set('job', job);
  else if (input) {
    p.set('input', input);
    if (retry) p.set('retry', retry);
    else if (force) p.set('force', '1');
  } else if (pr) p.set('pr', String(pr));
  else if (commit) p.set('commit', commit);
  return `?${p.toString()}`;
}

/* 저장소가 바뀌면 이전 PR·입력·작업은 가져가지 않는다 */
export const routeForRepo = (repo) => buildRoute({ repo });

export const sourceLabel = (input) => input?.source_type === 'commit' ? `커밋 ${shortSha(input.head_sha)}` : `PR #${input?.pr_number}`;

/* ── 화면 세대(늦은 응답 폐기) ──
   begin()마다 이전 세대의 요청을 abort하고 새 token을 준다. token.current()가 false면 그 응답은 버린다. */
export function createGuard() {
  let gen = 0;
  let controller = null;
  return {
    begin() {
      gen += 1;
      if (controller) { try { controller.abort(); } catch { /* noop */ } }
      controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      const mine = gen;
      return { id: mine, signal: controller ? controller.signal : undefined, current: () => mine === gen };
    },
    get generation() { return gen; },
  };
}

/* ── 멱등 key: 같은 요청 내용이면 같은 key를 쓴다(더블클릭·재전송이 한 번만 접수된다) ── */
export function stableStringify(value) {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

export function createIntent(newKey) {
  let current = null;
  return {
    keyFor(payload) {
      const sig = stableStringify(payload);
      if (!current || current.sig !== sig) current = { sig, key: newKey() };
      return current.key;
    },
    /* 서버가 응답했다면(성공·거절) 의도가 끝났다. 네트워크 오류·5xx는 같은 key로 다시 보내도록 남긴다. */
    settle(status) {
      if (status != null && status !== 0 && status < 500) current = null;
    },
    reset() { current = null; },
  };
}

/* 접수 여부를 알 수 없는 오류: 네트워크 오류·5xx·깨진 응답. 서버가 처리했을 수 있으므로 같은 멱등 key로 다시 보내야 한다. */
export function isAmbiguousError(err) {
  const status = err?.status ?? 0;
  return status === 0 || status >= 500 || err?.code === 'NETWORK' || err?.code === 'BAD_RESPONSE';
}

/* 접수 응답이 올바른가: 작업·입력 id가 uuid이고 지금 보는 저장소의 것이어야 한다 */
export function isAcceptedFor(out, idField, repo) {
  return !!out && typeof out === 'object' && toUuid(out[idField]) != null && out.repository_id === repo;
}

/* 접수 요청(POST /inputs·/jobs·/retry) 한 번.
   - key는 요청 내용으로 정한다(같은 내용 = 같은 key).
   - 응답을 검증하기 전에는 key를 버리지 않는다. 202인데 JSON이 깨졌거나 `{}`처럼 틀린 응답, 네트워크·5xx 오류는
     "접수됐는지 모른다"이므로 key를 유지해 같은 key로 다시 보낸다(서버가 같은 작업을 돌려준다 — 새 작업·비용이 생기지 않는다).
   - 서버가 분명히 거절했을 때(4xx 오류 응답)와 검증된 접수 응답을 받았을 때만 key를 끝낸다. */
export async function submitWithIntent(intent, payload, send, validate) {
  const key = intent.keyFor(payload);
  let out;
  try {
    out = await send(key);
  } catch (err) {
    if (err?.name === 'AbortError') throw err;                         // 취소는 서버 결과를 모른다: key 유지
    intent.settle(isAmbiguousError(err) ? 0 : err.status);
    throw err;
  }
  if (!validate(out)) {
    throw Object.assign(new Error('접수 응답을 확인할 수 없습니다.'), { status: 0, code: 'BAD_RESPONSE', details: {} });   // key 유지
  }
  intent.settle(200);
  return out;
}

/* ── 표시 포맷 ── */
export const shortSha = (sha) => (typeof sha === 'string' && sha ? sha.slice(0, 7) : '—');

export function fmtMoney(amount, currency = 'USD') {
  const n = Number(amount);
  if (amount == null || amount === '' || !Number.isFinite(n)) return '알 수 없음';
  const digits = n === 0 ? 2 : n < 0.01 ? 4 : 2;
  return `${currency === 'USD' ? '$' : `${currency} `}${n.toFixed(digits)}`;
}

export function fmtDuration(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}분 ${s % 60}초` : `${Math.floor(m / 60)}시간 ${m % 60}분`;
}

export function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / 1024 / 1024).toFixed(1)} MB`;
}

export const fmtTokens = (n) => (Number.isFinite(Number(n)) ? Number(n).toLocaleString('en-US') : '—');

/* ── 제외·누락 사유 ── */
export const LIMITATION_LABEL = {
  not_selected: '선택하지 않은 파일',
  unsupported_language: '지원하지 않는 언어',
  too_large: '너무 큰 파일/변경',
  truncated: '일부만 잘림',
  generated_file: '생성된 파일',
  binary: '바이너리·텍스트가 아닌 파일',
  link_or_submodule: '심볼릭 링크·서브모듈',
  secret_suspected: '비밀 정보로 의심돼 제외',
  fetch_failed: '코드를 가져오지 못함',
  analysis_failed: '분석 실패',
};
export const limitationLabel = (code) => LIMITATION_LABEL[code] || `알 수 없는 사유(${String(code).slice(0, 40)})`;

/* read는 "코드를 확보했다"는 뜻이다. 아직 어디로도 보내지 않았으므로 "전송됨"이라고 쓰지 않는다.
   준비가 끝나 실행할 수 있으면 "전송 예정", 그렇지 않으면(준비 중·만료) "코드 확보됨". */
export const CONTENT_STATE_LABEL = { read: '코드 확보됨', excluded: '제외됨', failed: '가져오기 실패' };
export function contentStateLabel(state, previewStatus) {
  if (state === 'read') return previewStatus === 'ready' ? '전송 예정' : CONTENT_STATE_LABEL.read;
  return CONTENT_STATE_LABEL[state] || String(state);
}
export const CHANGE_LABEL = { added: '추가', modified: '수정', deleted: '삭제', renamed: '이름 변경' };

/* ── coverage: 목록(전체 변경) / 선택 코드 확보 / 분석을 따로 말한다 ── */
const SELECTED_TEXT = { complete: '선택한 파일의 코드를 모두 확보', partial: '선택한 파일 중 일부만 코드를 확보', none: '코드를 확보하지 못함' };
const ANALYSIS_DONE = { complete: '선택한 파일 전체를 분석', partial: '선택한 파일 중 일부만 분석', none: '분석된 범위 없음' };
const INVENTORY_TEXT = { complete: '전체 변경 파일 목록을 확인', partial: '전체 변경 파일 목록의 일부만 확인(GitHub 한도)', unknown: '전체 변경 파일 수를 알 수 없음' };

export function scopeSummary(cov, { analyzed = null } = {}) {
  if (!cov) return '범위 정보 없음';
  const total = cov.pr_total_files == null ? '알 수 없음' : `${cov.pr_total_files}개`;
  const parts = [`전체 변경 ${total}`, `선택 ${cov.selected_files}개`];
  if (analyzed != null) parts.push(`분석 ${analyzed}개`);
  return parts.join(' · ');
}

/* beforeRun=true: 아직 실행 전이라 analysis_coverage=none은 "분석 전"이지 "분석 실패"가 아니다 */
export function coverageLines(cov, { beforeRun = false } = {}) {
  if (!cov) return [];
  const analysis = beforeRun && cov.analysis_coverage === 'none'
    ? '아직 분석 전(실행하면 이 범위가 분석됩니다)'
    : ANALYSIS_DONE[cov.analysis_coverage] || '알 수 없음';
  return [
    { label: '전체 변경 목록', value: INVENTORY_TEXT[cov.inventory_coverage] || '알 수 없음' },
    { label: '선택한 코드', value: SELECTED_TEXT[cov.selected_content_coverage] || '알 수 없음' },
    { label: '분석 범위', value: analysis },
  ];
}

export const coverageLimitations = (cov) => (cov?.limitations || []).map(limitationLabel);

/* ── 전송 동의 ── */
export function consentBlockers(preview) {
  const out = [];
  if (!preview) return ['미리보기를 불러오지 못했습니다.'];
  if (preview.status === 'building') out.push('코드를 준비하는 중입니다.');
  else if (preview.status !== 'ready') out.push(preview.status === 'expired'
    ? '입력의 보관·실행 기간이 지났거나 원문이 삭제됐습니다. 새로 준비해야 합니다.'
    : '입력 준비가 끝나지 않았거나 실패했습니다.');
  if (preview.status === 'ready' && !preview.execution_valid) out.push('실행 유효기간이 지났습니다. 새로 준비해야 합니다.');
  if (preview.status === 'ready' && !preview.reviewable) out.push('AI에 보낼 수 있는 파일이 없습니다(모두 제외됨). 다른 파일을 선택하세요.');
  const t = preview.transfer;
  if (!t) out.push('전송 대상(제공자·모델)을 확인할 수 없습니다.');
  else {
    if (t.execution_location !== 'local' && !t.external_transfer_allowed) out.push('이 서버는 코드의 외부 전송을 허용하지 않습니다.');
    if (t.requires_api_key !== false && !t.credential_configured) out.push(`${t.provider} API key를 먼저 등록해야 합니다.`);
  }
  if (preview.token_estimate && preview.token_estimate.within_limit === false) {
    out.push('선택한 모델의 입력 토큰 한도를 넘습니다. 파일을 줄이거나 다른 모델을 고르세요.');
  }
  return out;
}

/* 미리보기가 준 값을 그대로 동의 본문으로 쓴다(화면이 값을 조합하지 않는다) */
export function buildConsent(preview) {
  if (!preview || consentBlockers(preview).length) return null;
  const t = preview.transfer;
  return {
    input_id: preview.input_id, manifest_hash: preview.manifest_hash, provider: t.provider, profile_id: t.profile_id,
    credential_version: t.credential_version, policy_version: preview.policy_version,
    ...(t.profile_fingerprint ? { profile_fingerprint: t.profile_fingerprint } : {}),
  };
}

export function consentStatement(preview) {
  const t = preview?.transfer;
  if (!t) return '';
  const files = (preview.files || []).filter((f) => f.content_state === 'read').length;
  if (t.execution_location === 'local') return `선택한 ${files}개 파일의 코드와 변경 제목·설명을 같은 PC의 ${t.model}에서 처리하는 데 동의합니다. 외부 AI 제공자에게 전송하지 않으며 API 요금은 없습니다(전력·하드웨어 비용 별도). ${t.model_digest ? `모델 digest: ${t.model_digest}.` : '모델 ID를 고정합니다. 이 런타임의 모델 파일 해시는 검증하지 않습니다.'}`;
  return `선택한 ${files}개 파일의 코드(변경 diff와 주변 줄)와 변경 제목·설명이 ${t.provider} / ${t.model}(으)로 전송되는 것에 동의합니다. `
    + `사용할 키: ${t.key_hint || '—'} (v${t.credential_version ?? '?'}), 전송 정책 ${preview.policy_version}.`;
}

/* ── 오류 → 안내 ── */
export function describeError(err) {
  const status = err?.status ?? 0;
  const code = err?.code || null;
  const reason = err?.details?.reason || null;
  const out = (title, body, action = null, tone = 'error') => ({ title, body, action, tone, status, code, retryAfter: err?.retryAfter ?? null });
  if (status === 0 || code === 'NETWORK') return out('서버에 연결할 수 없습니다', '네트워크를 확인하고 잠시 후 다시 시도해 주세요.', 'retry');
  if (code === 'BAD_RESPONSE') return out('서버 응답을 해석할 수 없습니다', '일시적인 문제일 수 있습니다. 잠시 후 다시 시도해 주세요.', 'retry');
  switch (code) {
    case 'AUTH_REQUIRED': return out('로그인이 필요합니다', 'GitHub로 로그인한 뒤 다시 열어 주세요. 진행 중이던 선택은 저장되지 않습니다.', 'login');
    case 'REAUTH_REQUIRED': return out('GitHub 인증을 갱신해야 합니다', '다시 로그인하면 이어서 진행할 수 있습니다.', 'login');
    case 'CSRF_FAILED': return out('요청을 확인할 수 없습니다', '페이지를 새로고침한 뒤 다시 시도해 주세요.', 'reload');
    case 'RESOURCE_NOT_FOUND':
      if (reason === 'JOB_DELETED') return out('삭제된 작업입니다', '이 요청으로 만들어진 작업은 삭제됐습니다. 필요하면 새로 요청해 주세요.', 'home');
      return out('열 수 없습니다', '접근 권한이 없거나, 삭제됐거나, 이 서버에서 AI 기능을 쓸 수 없습니다. 어느 쪽인지는 알려 드리지 않습니다.', 'home');
    case 'TRANSFER_NOT_ALLOWED': return out('코드를 외부로 보낼 수 없습니다', '서버 설정, 비공개 저장소, 또는 전송 정책 때문에 허용되지 않았습니다.', null);
    case 'PR_CHANGED': return out('PR이 바뀌었습니다', '선택한 시점 이후 PR의 SHA가 달라졌습니다. 파일을 새 SHA로 다시 선택하세요.', 'reselect');
    case 'PREVIEW_CHANGED': return out('확인한 내용과 달라졌습니다', '코드 범위·모델·API key 중 하나가 바뀌었습니다. 미리보기를 다시 확인하고 다시 동의해 주세요.', 'reload_preview', 'warn');
    case 'VERSION_CONFLICT': return out('다른 곳에서 먼저 바뀌었습니다', '최신 내용을 다시 불러왔습니다. 확인 후 다시 저장해 주세요.', 'reload_findings', 'warn');
    case 'IDEMPOTENCY_CONFLICT': return out('같은 요청 번호로 다른 요청을 보냈습니다', '화면을 새로고침한 뒤 다시 시도해 주세요.', 'reload');
    case 'INPUT_EXPIRED': return out('입력이 만료됐거나 삭제됐습니다', '원문 코드가 더 이상 없어 이 입력으로는 실행할 수 없습니다. 파일을 다시 선택해 새로 준비하세요.', 'reselect');
    case 'RESULT_EXPIRED': return out('결과 보관 기간이 지났습니다', '이 결과는 더 이상 열 수 없습니다. 필요하면 다시 실행해 주세요.', 'home');
    case 'INPUT_TOO_LARGE': return out('너무 큽니다', '선택한 파일이 많거나 커서 처리할 수 없습니다. 파일 수를 줄이거나 더 큰 한도의 모델을 고르세요.', null);
    case 'CREDENTIAL_REQUIRED': return out('API key가 필요합니다', '제공자 API key를 등록한 뒤 다시 시도해 주세요.', 'keys');
    case 'CREDENTIAL_INVALID': return out('API key를 쓸 수 없습니다', reason === 'model_unavailable'
      ? '이 key로는 선택한 모델을 쓸 수 없습니다. 다른 모델을 고르거나 key를 확인하세요.'
      : '제공자가 key를 거부했습니다. key와 결제 상태를 확인해 주세요.', 'keys');
    case 'INVALID_SELECTION': return out('요청 내용이 올바르지 않습니다', '선택한 값을 확인해 주세요.', null);
    case 'INVALID_STATE':
      if (reason === 'NO_REVIEWABLE_CONTENT') return out('AI에 보낼 파일이 없습니다', '선택한 파일이 모두 제외돼 보낼 코드가 없습니다. 다른 파일을 선택하세요.', 'reselect');
      return out('지금은 할 수 없는 작업입니다', '현재 상태에서 허용되지 않는 요청입니다. 화면을 새로고침해 상태를 확인하세요.', 'reload');
    case 'USER_LIMIT': return out('요청 한도에 도달했습니다', '오늘의 AI 요청 한도(또는 키 확인 한도)를 모두 썼습니다. 나중에 다시 시도해 주세요.', null, 'warn');
    case 'BUDGET_LIMIT': return out('사용 예산 한도에 도달했습니다', '이번 달 개인 사용 한도를 넘게 됩니다. 사용량을 확인해 주세요.', 'usage', 'warn');
    case 'UPSTREAM_RATE_LIMIT': return out('요청이 너무 많습니다', '잠시 기다린 뒤 다시 시도해 주세요.', 'retry', 'warn');
    case 'ACCESS_CHECK_UNAVAILABLE': return out('저장소 접근 권한을 확인하지 못했습니다', '일시적인 문제일 수 있습니다. 잠시 후 다시 시도해 주세요.', 'retry', 'warn');
    case 'UPSTREAM_UNAVAILABLE': return out('외부 서비스가 일시적으로 응답하지 않습니다', '잠시 후 다시 시도해 주세요.', 'retry', 'warn');
    default:
      if (status === 401) return out('로그인이 필요합니다', 'GitHub로 로그인한 뒤 다시 열어 주세요.', 'login');
      if (status === 404) return out('열 수 없습니다', '접근 권한이 없거나 삭제됐습니다.', 'home');
      if (status === 429) return out('요청이 너무 많습니다', '잠시 기다린 뒤 다시 시도해 주세요.', 'retry', 'warn');
      if (status >= 500) return out('서버에 문제가 있습니다', '잠시 후 다시 시도해 주세요.', 'retry');
      return out('요청을 처리하지 못했습니다', '잠시 후 다시 시도해 주세요.', null);
  }
}

/* ── 작업 상태 ── */
export const STAGE_LABEL = {
  queued: '대기 중', input_preparation: '코드·권한 확인 중', provider_call: '모델 호출 중',
  output_validation: '출력 검증·저장 중', result_storage: '결과 저장 중',
};
export const KIND_LABEL = { summary: '변경 설명', review: '코드 리뷰', prepare: '입력 준비' };

/* 작업 error_code별 안내. retry=true이면 사용자가 명시적으로 다시 실행할 수 있다(자동 재실행은 없다). */
export const JOB_ERRORS = {
  OUTCOME_UNCERTAIN: { title: '결과를 알 수 없습니다', body: '제공자가 요청을 처리했는지 확인하지 못했습니다. 비용이 이미 발생했을 수 있습니다. 자동으로 다시 실행하지 않았습니다. 다시 실행하면 비용이 또 들 수 있습니다.', retry: true, costRisk: true },
  PROVIDER_TIMEOUT: { title: '제공자 응답을 받지 못했습니다', body: '요청이 처리됐는지 알 수 없어 자동으로 다시 실행하지 않았습니다. 비용이 발생했을 수 있습니다.', retry: true, costRisk: true },
  RESULT_LOST: { title: '결과를 저장하지 못했습니다', body: '모델 호출은 처리·정산됐지만 처리 도중 결과를 저장하지 못했습니다. 같은 요청을 다시 실행하면 비용이 다시 듭니다.', retry: true, costRisk: true },
  RESULT_STORAGE_FAILED: { title: '결과 저장에 실패했습니다', body: '모델 호출은 끝났지만 결과를 저장하지 못했습니다. 다시 실행하면 비용이 다시 듭니다.', retry: true, costRisk: true },
  PROVIDER_ERROR: { title: '제공자 호출 결과를 알 수 없습니다', body: '분류할 수 없는 오류가 났고 비용이 발생했을 수 있습니다.', retry: true, costRisk: true },
  OUTPUT_VALIDATION_FAILED: { title: '모델 출력이 검증을 통과하지 못했습니다', body: '파일·줄 참조나 형식이 맞지 않아 결과를 저장하지 않았습니다. 호출 비용은 발생했습니다.', retry: true, costRisk: true },
  OUTPUT_INVALID: { title: '모델 출력을 쓸 수 없습니다', body: '출력이 비었거나 형식이 올바르지 않아 저장하지 않았습니다. 호출 비용은 발생했습니다.', retry: true, costRisk: true },
  OUTPUT_TRUNCATED: { title: '모델 출력이 잘렸습니다', body: '출력 한도에 걸려 결과를 쓸 수 없습니다. 호출 비용은 발생했습니다. 파일을 줄여 다시 시도해 보세요.', retry: true, costRisk: true },
  PROVIDER_REFUSED: { title: '모델이 요청을 거절했습니다', body: '안전 정책에 따라 거절됐습니다. 호출 비용은 발생했을 수 있습니다. 다른 모델을 고르면 새로 동의해야 합니다.', retry: true, costRisk: true },
  PROVIDER_AUTH: { title: 'API key가 거부됐습니다', body: '제공자가 key를 거부했습니다. key를 교체한 뒤 다시 실행해 주세요.', retry: true, action: 'keys' },
  PROVIDER_REJECTED: { title: '제공자가 요청을 거절했습니다', body: '모델을 쓸 수 없거나 요청이 허용되지 않았습니다. 비용은 발생하지 않았습니다.', retry: true },
  PROVIDER_RATE_LIMITED: { title: '제공자 요청 한도에 걸렸습니다', body: '재시도 한도를 넘었습니다. 잠시 후 다시 실행해 주세요. 비용은 발생하지 않았습니다.', retry: true },
  PROVIDER_UNAVAILABLE: { title: '제공자가 응답하지 않습니다', body: '재시도 한도를 넘었습니다. 잠시 후 다시 실행해 주세요. 비용은 발생하지 않았습니다.', retry: true },
  CREDENTIAL_UNAVAILABLE: { title: '사용할 API key가 없습니다', body: '이 작업에 고정된 key가 삭제됐거나 파기됐습니다. key를 다시 등록한 뒤 새로 실행해 주세요.', retry: true, action: 'keys' },
  PROFILE_CHANGED: { title: '모델 설정이 바뀌었습니다', body: '동의했을 때와 모델의 가격·한도가 달라져 실행하지 않았습니다. 새로 확인하고 동의해 주세요.', retry: true },
  PROFILE_NOT_ALLOWED: { title: '허용되지 않은 모델입니다', body: '관리자가 이 모델을 더 이상 허용하지 않습니다. 다른 모델을 선택해 다시 동의해 주세요.', retry: true },
  ACCESS_REVOKED: { title: '저장소 접근 권한을 잃었습니다', body: '실행 도중 권한이 없어져 결과를 공개하지 않았습니다. 호출 비용은 발생했을 수 있습니다.', retry: false, costRisk: true },
  REAUTH_REQUIRED: { title: 'GitHub 인증이 필요합니다', body: '다시 로그인한 뒤 새로 실행해 주세요.', retry: true, action: 'login' },
  TRANSFER_NOT_ALLOWED: { title: '코드를 외부로 보낼 수 없습니다', body: '서버 설정·비공개 저장소·정책 때문에 전송이 허용되지 않아 보내지 않았습니다.', retry: false },
  INPUT_EXPIRED: { title: '입력이 만료됐습니다', body: '실행 유효기간이 지나 보내지 않았습니다. 파일을 다시 선택해 새로 준비하세요.', retry: false, action: 'reselect' },
  PREVIEW_CHANGED: { title: '동의한 내용과 달라졌습니다', body: '동의한 코드 범위와 달라 보내지 않았습니다. 다시 확인하고 동의해 주세요.', retry: false, action: 'reselect' },
  INPUT_INTEGRITY: { title: '보관된 코드를 확인할 수 없습니다', body: '무결성 확인에 실패해 보내지 않았습니다. 새로 준비해 주세요.', retry: false, action: 'reselect' },
  NO_REVIEWABLE_CONTENT: { title: 'AI에 보낼 파일이 없습니다', body: '선택한 파일이 모두 제외돼 보낼 코드가 없습니다.', retry: false, action: 'reselect' },
  INPUT_TOO_LARGE: { title: '모델 입력 한도를 넘습니다', body: '파일을 줄이거나 더 큰 한도의 모델을 고르세요. 비용은 발생하지 않았습니다.', retry: false, action: 'reselect' },
  BUDGET_LIMIT: { title: '사용 예산 한도에 걸렸습니다', body: '호출하지 않았고 비용은 발생하지 않았습니다. 사용량을 확인해 주세요.', retry: true, action: 'usage' },
  INVALID_STATE: { title: '작업을 이어갈 수 없습니다', body: '작업 정보를 확인할 수 없어 중단했습니다.', retry: false },
  INTERNAL_ERROR: { title: '내부 오류로 중단됐습니다', body: '예상하지 못한 오류가 났습니다. 잠시 후 다시 시도해 주세요.', retry: true },
  ATTEMPTS_EXHAUSTED: { title: '재시도 횟수를 모두 썼습니다', body: '일시적 오류가 계속돼 중단했습니다. 잠시 후 다시 실행해 주세요.', retry: true },
  QUEUE_TIMEOUT: { title: '대기 시간이 너무 길었습니다', body: '대기열에서 제한 시간을 넘겨 시작하지 못했습니다. 비용은 발생하지 않았습니다.', retry: true },
  NO_HANDLER: { title: '처리할 수 있는 worker가 없습니다', body: '서버 설정 문제입니다. 관리자에게 알려 주세요.', retry: true },
};
const UNKNOWN_JOB_ERROR = { title: '작업이 실패했습니다', body: '알 수 없는 오류 코드입니다. 비용이 발생했는지 확인할 수 없으니 사용량을 확인하세요.', retry: true, costRisk: true };

function describeJobState(job) {
  if (!job) return null;
  const status = job.status;
  const stage = job.stage ? (STAGE_LABEL[job.stage] || job.stage) : null;
  const base = { status, stage, canCancel: false, canRetry: false, retryExplicit: false, tone: 'info', action: null };
  switch (status) {
    case 'queued': return { ...base, canCancel: true, title: '대기 중', body: '곧 시작합니다. 아직 아무것도 전송하지 않았습니다.' };
    case 'running': return { ...base, canCancel: true, title: stage || '실행 중', body: '' };
    case 'retry_wait': return { ...base, canCancel: true, tone: 'warn', title: '재시도 대기',
      body: `일시적인 오류로 ${job.retry_after_seconds != null ? `${fmtDuration(job.retry_after_seconds)} 뒤 ` : '잠시 뒤 '}자동으로 다시 시도합니다(호출이 처리되지 않았음이 확실한 경우만).` };
    case 'cancel_requested': return { ...base, tone: 'warn', title: '취소 접수됨',
      body: '진행 중인 호출은 끝날 수 있고 그 비용은 발생할 수 있습니다. 취소가 확정되면 결과를 저장하지 않습니다.' };
    case 'cancelled': return { ...base, tone: 'warn', canRetry: true, retryExplicit: true, title: '취소됨',
      body: '결과를 저장하지 않았습니다. 호출이 이미 시작됐다면 비용이 발생했을 수 있습니다.' };
    case 'succeeded': return { ...base, tone: 'ok', title: '완료', body: '결과가 저장됐습니다.' };
    case 'failed': {
      const g = JOB_ERRORS[job.error_code] || UNKNOWN_JOB_ERROR;
      const uncertain = job.outcome_uncertain === true;
      return { ...base, tone: g.costRisk || uncertain ? 'error' : 'error', title: g.title,
        body: uncertain && !g.costRisk ? `${g.body} 호출 결과를 알 수 없어 비용이 발생했을 수 있습니다.` : g.body,
        canRetry: g.retry, retryExplicit: g.retry, costRisk: !!(g.costRisk || uncertain), action: g.action || null };
    }
    default: return { ...base, title: String(status || '알 수 없음'), body: '' };
  }
}

export function usageStatement(usage) {
  const unlimited = Number(usage.user_limit) === 0;
  return `이번 달 사용 ${fmtMoney(usage.user_spent, usage.currency)}${unlimited ? '' : ` / 한도 ${fmtMoney(usage.user_limit, usage.currency)}`} (예약 ${fmtMoney(usage.user_reserved, usage.currency)}${unlimited ? '' : `, 남음 ${fmtMoney(usage.user_remaining, usage.currency)}`})`
    + ` · 오늘 요청 ${usage.daily_jobs_used}${usage.daily_job_limit === 0 ? '회' : `/${usage.daily_job_limit}`}`;
}

export function selectableProviders(providers = []) {
  const local = providers.filter((p) => p.models.some((m) => m.execution_location === 'local'));
  const external = providers.filter((p) => !local.includes(p));
  return local.length ? [...external, { id: 'local', display_name: '로컬 AI', key_format: 'API key 불필요',
    models: local.flatMap((p) => p.models.map((m) => ({ ...m,
      display_name: `${p.display_name.replace(' (같은 PC)', '')} · ${m.model || '모델 설정 필요'}` }))) }] : external;
}

export function describeJob(job) {
  const description = describeJobState(job);
  if (!description || !['ollama', 'lmstudio', 'koboldcpp'].includes(job.provider)) return description;
  if (['cancel_requested', 'cancelled'].includes(job.status)) {
    return { ...description, costRisk: false,
      body: '로컬 호출이 이미 시작됐다면 연산은 끝날 수 있습니다. 취소 후 결과는 저장하지 않습니다. 제공자 API 요금은 없습니다.' };
  }
  if (description.costRisk) {
    return { ...description, costRisk: false,
      body: `${description.title}. 로컬 연산의 결과를 공개하지 않았습니다. 자동으로 다시 실행하지 않습니다. 제공자 API 요금은 없으며, 재실행하면 연산을 반복할 수 있습니다.` };
  }
  return description;
}

export const isTerminalStatus = (s) => s === 'succeeded' || s === 'failed' || s === 'cancelled';
export const jobSignature = (j) => [j.status, j.stage, j.cancel_requested, j.retry_after_seconds, j.error_code, j.result_id].join('|');
export const inputSignature = (p) => [p.status, p.prepare_job?.status, p.prepare_job?.stage, p.prepare_job?.error_code].join('|');

/* ── 결과 ── */
export function findingsSummary(findings) {
  const out = { total: 0, unverified: 0, valid: 0, false_positive: 0, uncertain: 0 };
  for (const f of findings || []) {
    out.total += 1;
    const v = f?.finding?.human_validation || 'unverified';
    out[v in out ? v : 'unverified'] += 1;
  }
  return out;
}

export function humanValidationText(summary) {
  if (!summary.total) return '사람이 검증한 지적 없음(지적 0건)';
  const parts = [];
  if (summary.valid) parts.push(`유효 ${summary.valid}`);
  if (summary.false_positive) parts.push(`오탐 ${summary.false_positive}`);
  if (summary.uncertain) parts.push(`불확실 ${summary.uncertain}`);
  parts.push(`미검증 ${summary.unverified}`);
  return `사람 검증 ${summary.total - summary.unverified}/${summary.total} · ${parts.join(' · ')}`;
}

export const STRUCTURE_NOTE = '구조·참조 검증을 통과했다는 뜻입니다. 내용이 옳다는 보증이 아닙니다.';
export const MODEL_NOTE = '모델이 만든 결과이며 사람이 검증하기 전까지는 참고용입니다.';

/* 지적 0건: "오류 없음"으로 읽히지 않게, 분석 범위와 검증 상태를 함께 말한다 */
export function emptyFindingsText(cov) {
  const analyzed = cov ? ANALYSIS_DONE[cov.analysis_coverage] || '분석 범위 알 수 없음' : '분석 범위 알 수 없음';
  return `지적 0건 — ${analyzed}(${scopeSummary(cov)}). 모델이 보고한 지적이 없다는 뜻이며 코드에 문제가 없다는 뜻이 아닙니다. 이 결과는 사람이 검증하기 전입니다.`;
}

export const FORBIDDEN_PHRASES = ['오류 없음', '문제 없음', '이상 없음', '버그 없음', '안전합니다', 'no issues', 'no errors'];

export function staleShaNotice(resultHeadSha, currentHeadSha) {
  if (!resultHeadSha || !currentHeadSha) return null;
  if (resultHeadSha === currentHeadSha) return null;
  return `이 결과는 이전 SHA(${shortSha(resultHeadSha)})의 코드를 분석했습니다. PR의 현재 head는 ${shortSha(currentHeadSha)}입니다. 지금 코드에는 그대로 맞지 않을 수 있습니다.`;
}

export const VERDICT_LABEL = { valid: '유효', false_positive: '오탐', uncertain: '불확실', unverified: '미검증' };
export const SIDE_LABEL = { LEFT: '변경 전(LEFT)', RIGHT: '변경 후(RIGHT)' };

export function locationText(loc) {
  if (!loc) return '';
  const range = loc.start_line === loc.end_line ? `${loc.start_line}` : `${loc.start_line}-${loc.end_line}`;
  return `${loc.path}:${range} (${SIDE_LABEL[loc.side] || loc.side})`;
}

/* ── GitViz 이력 근거(S11): 분석 snapshot에서 옮긴 값만 보여 준다. 코드 지적과 섞지 않고, 누락·오류로 단정하지 않는다. ── */
export const HISTORY_NOTE = '아래는 GitViz 분석 snapshot에 저장된 이력 통계입니다. AI가 만든 지적이 아니며 코드가 틀렸다는 근거도 아닙니다.';
export const HISTORY_STATUS_LABEL = { observed: '이력 관측됨', low_support: '관측 적음', no_history: '이력 없음', no_observations: '관측 기간 내 변경 없음', not_in_snapshot: 'snapshot에 없음' };

export function historyEvidenceSummary(row) {
  const ev = row.evidence || {};
  const basis = ev.basis || {};
  if (row.status !== 'ok') {
    return { excluded: true, reasons: row.exclusion_reasons || [], notes: ev.notes || [], paths: [], basis, quality: [] };
  }
  const q = ev.data_quality || {};
  const quality = [
    ['커밋', q.commit_count], ['작성자 미식별 커밋', q.unidentified_commits], ['기여자', q.contributor_count],
    ['병합 PR', q.merged_pr_count], ['함께 변경 단위', q.co_change_unit]].filter(([, v]) => v !== undefined && v !== null)
    .map(([label, value]) => ({ label, value: String(value) }));
  const paths = (ev.paths || []).map((p) => ({
    path: p.path, matched: p.match === 'old_path' ? p.matched_path : null, status: HISTORY_STATUS_LABEL[p.status] || p.status,
    observations: p.observations ? [`변경 ${p.observations.change_freq}회`, `churn ${p.observations.churn}`, `버그 수정 ${p.observations.bugfix_count}회`] : [],
    ownership: p.ownership ? [`관측 ${p.ownership.observation_count}건`, `기여자 ${p.ownership.contributor_count}명`, `최다 기여 비중 ${Math.round((p.ownership.top_owner_share || 0) * 100)}%`] : [],
    coChange: (p.co_change || []).map((c) => ({ path: c.path, count: c.co_change_count, changedInPr: !!c.changed_in_pr, lowSupport: !!c.low_support })),
    limitations: p.limitations || [] }));
  return { excluded: false, reasons: [], notes: ev.notes || [], paths, basis, quality };
}
