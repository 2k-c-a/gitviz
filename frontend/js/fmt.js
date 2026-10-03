/* ── 표시용 포맷 도우미 (순수 함수) ──
   null은 0으로 바꾸지 않는다: 값이 없으면 항상 '—'를 돌려주고, 이유는 호출부가 따로 적는다. */

export const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export const DASH = '—';

/* 정수 등 일반 숫자. null/undefined → '—' (0과 구분) */
export const num = (n) => (n == null ? DASH : Number(n).toLocaleString('en-US'));

/* 0~1 점수 등 소수. null → '—' */
export const fmtScore = (v, digits = 2) => (v == null || Number.isNaN(Number(v)) ? DASH : Number(v).toFixed(digits));

/* 점수 표기 — 보통 2자리. 다만 0.3/0.4/0.5/0.7 같은 표시 임계값 바로 아래 값이 반올림으로 임계값 위처럼 보이면
   (예: 0.6984 → '0.70'인데 색·배지는 0.7 미만) 3자리로 보여 모순을 없앤다. */
const BOUNDARIES = [0.3, 0.4, 0.5, 0.7];
export function fmtScoreStable(v) {
  if (v == null || Number.isNaN(Number(v))) return DASH;
  const n = Number(v);
  const two = n.toFixed(2);
  const crosses = BOUNDARIES.some(b => (Number(two) >= b) !== (n >= b));
  return crosses ? n.toFixed(3) : two;
}

/* 부호 있는 차이값. null → '—', 0은 '0.00' (변화 없음과 자료 없음 구분) */
export function fmtDelta(v, digits = 2) {
  if (v == null) return DASH;
  const n = Number(v);
  if (n === 0) return (0).toFixed(digits);
  return `${n > 0 ? '+' : '−'}${Math.abs(n).toFixed(digits)}`;
}

/* 비율(0~1) → 사람이 읽는 퍼센트. 1% 미만은 '<1%'로 두어 0%와 구분한다.
   10% 미만은 소수 첫째 자리까지, 이상은 정수. */
export function fmtPct(ratio) {
  if (ratio == null || Number.isNaN(Number(ratio))) return DASH;
  const p = Number(ratio) * 100;
  if (p === 0) return '0%';
  if (p < 1) return '<1%';
  if (p >= 10) return `${Math.round(p)}%`;
  return `${String(Math.round(p * 10) / 10)}%`;
}

const pad = (n) => String(n).padStart(2, '0');

export function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())}`;
}

export function fmtDateTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${fmtDate(iso)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* 관측 기간 표기. window_start가 null이면 '전체 이력'(이전 계산 버전) — 구간 끝은 포함하지 않는다. */
export function fmtWindow(snap) {
  if (!snap) return '';
  const start = snap.window_start ? fmtDate(snap.window_start) : '전체 이력';
  return `${start} ~ ${fmtDate(snap.window_end)}`;
}

export function windowDays(snap) {
  if (!snap || !snap.window_start || !snap.window_end) return null;
  const ms = new Date(snap.window_end).getTime() - new Date(snap.window_start).getTime();
  return Number.isFinite(ms) ? Math.round(ms / 86400000) : null;
}

export function fmtSeconds(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}시간 ${m % 60}분`;
  if (m > 0) return `${m}분 ${s % 60}초`;
  return `${s}초`;
}
