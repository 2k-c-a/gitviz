// node tests/frontend/test_schedule_participants.cjs — 일정 참여자 '전원 선택' 버튼 상태 검증(DOM 없음)
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

const stub = (name, value) => Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
stub('window', globalThis);
stub('document', { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], addEventListener() {} });
stub('localStorage', { getItem: () => null, setItem() {}, removeItem() {} });
stub('location', { search: '', pathname: '/pages/community.html', href: 'http://localhost/pages/community.html' });

const load = (f) => import(pathToFileURL(path.resolve(__dirname, '..', '..', 'frontend', 'js', f)).href);

async function main() {
  const { participantSelection, MAX_PARTICIPANTS } = await load('community-schedule.js');
  assert.equal(MAX_PARTICIPANTS, 30, '서버 계약 participant_ids maxItems와 같아야 한다');

  let v = participantSelection(4, 0);
  assert.deepEqual([v.label, v.selectAll, v.disabled, v.summary, v.note], ['전원 선택', true, false, '참여자 0/4명 선택', '']);

  v = participantSelection(4, 2);
  assert.equal(v.label, '전원 선택'); assert.equal(v.selectAll, true);

  v = participantSelection(4, 4);
  assert.deepEqual([v.label, v.selectAll, v.disabled], ['전원 해제', false, false]);

  v = participantSelection(0, 0);
  assert.equal(v.disabled, true, '멤버가 없으면 누를 수 없다');

  v = participantSelection(31, 3);
  assert.equal(v.disabled, true, '상한을 넘는 전원 선택은 막는다');
  assert.match(v.note, /최대 30명/);

  v = participantSelection(30, 0);
  assert.equal(v.disabled, false, '상한과 같은 인원은 전원 선택할 수 있다');
  console.log('test_schedule_participants: ok');
}
main().catch((error) => { console.error(error); process.exit(1); });
