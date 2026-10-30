/* Web keeps same-origin fetch. Desktop sends typed endpoint data to main. */
export const isDesktop = () => !!globalThis.desktop?.remote?.request;
let catalog;
let hadAccount = false;
function clearRemoteState() {
  hadAccount = false;
  for (const storage of [globalThis.localStorage, globalThis.sessionStorage]) {
    if (storage) for (const key of Object.keys(storage)) if (key.startsWith('gitviz_')) storage.removeItem(key);
  }
  globalThis.dispatchEvent?.(new Event('gitviz-account-changed'));
  if (globalThis.document) {
    const notice = document.createElement('p'); notice.textContent = '원격 로그인이 만료됐습니다. 다시 로그인하세요.';
    document.body.replaceChildren(notice);
    location.replace('/index.html');
  }
}
export async function endpointRequest(input, options = {}, definitions) {
  const base = new URL('https://transport.invalid');
  const url = new URL(String(input), base);
  if (url.origin !== base.origin || url.hash || /[\\]/.test(String(input))) throw new Error('INVALID_REQUEST');
  const method = (options.method || 'GET').toUpperCase();
  for (const [endpointKey, definition] of Object.entries(definitions)) {
    if (definition.method !== method) continue;
    const names = [];
    const pattern = definition.template.replace(/[.*+?^$()|[\]\\]/g, '\\$&').replace(/\{(\w+)\}/g, (_, name) => { names.push(name); return '([^/]+)'; });
    const match = url.pathname.match(new RegExp(`^${pattern}$`));
    if (!match) continue;
    const path = Object.fromEntries(names.map((name, index) => [name, definition.path[name] === 'id' ? Number(match[index + 1]) : decodeURIComponent(match[index + 1])]));
    const query = {};
    for (const [key, value] of url.searchParams) {
      if (!(key in definition.query) || key in query) throw new Error('INVALID_REQUEST');
      query[key] = ['id', 'number'].includes(definition.query[key]) ? Number(value) : value;
    }
    const headers = new Headers(options.headers || {});
    if ([...headers.keys()].some(key => !['accept', 'content-type', 'idempotency-key', 'x-csrf-token'].includes(key))) throw new Error('INVALID_REQUEST');
    const result = { endpointKey, path, query };
    if (options.body !== undefined) result.body = JSON.parse(options.body);
    if (headers.has('idempotency-key')) result.idempotencyKey = headers.get('idempotency-key');
    return result;
  }
  throw new Error('ENDPOINT_NOT_ALLOWED');
}

export async function request(input, options = {}) {
  if (!isDesktop()) return fetch(input, options);
  if (options.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  catalog ??= fetch('/desktop-endpoints.json', { cache: 'no-store' }).then(res => { if (!res.ok) throw new Error('UNAVAILABLE'); return res.json(); });
  const typed = await endpointRequest(input, options, await catalog);
  let result;
  try { result = await globalThis.desktop.remote.request(typed); }
  catch (error) {
    if (hadAccount && /AUTH_REQUIRED|ACCOUNT_CHANGED/.test(error?.message || '')) clearRemoteState();
    throw error;
  }
  if (result.status === 401 && hadAccount) clearRemoteState();
  else if (result.status >= 200 && result.status < 300) hadAccount = true;
  if (options.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  return new Response(result.status === 204 ? null : JSON.stringify(result.body), { status: result.status, headers: { 'Content-Type': 'application/json', ...result.headers } });
}

export async function startLogin() {
  const state = await globalThis.desktop.auth.start();
  let notice = document.getElementById('desktop-auth-notice');
  if (!notice) { notice = document.createElement('p'); notice.id = 'desktop-auth-notice'; document.body.prepend(notice); }
  notice.textContent = state.state === 'pending' ? `브라우저에서 계정과 확인 코드 ${state.displayCode}를 비교하고 승인하세요.` : '로그인 상태를 확인합니다.';
  while (state.state === 'pending') {
    await new Promise(resolve => setTimeout(resolve, Math.max(2000, state.pollAfterMs || 2000)));
    Object.assign(state, await globalThis.desktop.auth.status());
  }
  notice.textContent = state.state === 'authenticated' ? '로그인했습니다.' : '로그인을 완료하지 않았습니다.';
  if (state.state === 'authenticated') location.reload();
}
