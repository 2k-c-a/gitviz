// AI defaults (model, purpose) live in this window's localStorage; API keys go to main, which stores them with DPAPI.
const api = window.workspace;
const STORE = 'gitviz.workspace.ai';
const profile = document.getElementById('settings-ai-profile');
const purpose = document.getElementById('settings-ai-purpose');
const provider = document.getElementById('settings-ai-provider');
const key = document.getElementById('settings-ai-key');
const status = document.getElementById('settings-ai-key-status');
const message = document.getElementById('settings-ai-message');
const FALLBACK = { profileId: profile.value, purpose: purpose.value };
let saved = { anthropic: false, openai: false, gemini: false };

function read() { try { return { ...FALLBACK, ...JSON.parse(localStorage.getItem(STORE) || '{}') }; } catch { return { ...FALLBACK }; } }
function write() { try { localStorage.setItem(STORE, JSON.stringify({ profileId: profile.value, purpose: purpose.value })); } catch { /* Defaults then last only for this session. */ } }
/** Profile and purpose used by the one-click review and refactor buttons. */
export function aiDefaults() { return { profileId: profile.value, purpose: purpose.value.trim() || FALLBACK.purpose }; }
function code(error) { return String(error?.message || error).replace(/^.*Error invoking remote method.*Error: /, ''); }
function render() {
  const has = saved[provider.value];
  status.textContent = has ? '저장됨' : '저장 안 됨'; status.dataset.tone = has ? 'ok' : 'warn';
  document.getElementById('settings-ai-key-delete').disabled = !has;
}
async function refresh() {
  try { saved = await api.getAiKeyStatus(); render(); }
  catch (error) { message.textContent = code(error) === 'STORAGE_UNAVAILABLE' ? 'Windows 계정 보호 저장소를 쓸 수 없어 키를 저장·확인할 수 없습니다.' : code(error); }
}
const initial = read();
if ([...profile.options].some(option => option.value === initial.profileId)) profile.value = initial.profileId;
purpose.value = initial.purpose;
profile.addEventListener('change', write);
purpose.addEventListener('change', write);
provider.addEventListener('change', render);
document.getElementById('settings-ai-key-save').addEventListener('click', async event => {
  const value = key.value.trim(); key.value = '';
  if (!value) { message.textContent = '저장할 API 키를 입력하세요.'; return; }
  event.target.disabled = true;
  try { saved = await api.saveAiKey({ provider: provider.value, apiKey: value }); render(); message.textContent = '키를 암호화해 저장했습니다.'; }
  catch (error) { message.textContent = code(error) === 'INVALID_REQUEST' ? 'API 키 형식이 올바르지 않습니다.' : code(error); }
  finally { event.target.disabled = false; }
});
document.getElementById('settings-ai-key-delete').addEventListener('click', async () => {
  try { saved = await api.deleteAiKey({ provider: provider.value }); render(); message.textContent = '저장한 키를 삭제했습니다.'; }
  catch (error) { message.textContent = code(error); }
});
render(); void refresh();
