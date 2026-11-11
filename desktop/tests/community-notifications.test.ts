import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  badgeOf, chatToast, CommunityNotifier, communityUrl, inviteToasts, parseSummary, showsChat, whenText,
  type EventTimer, type NotificationSummary, type Toast,
} from '../src/community-notifications';
import { endpoints } from '../src/remote-contracts';

const manual: EventTimer = { set: () => 1, clear: () => {} };
const person = (login: string) => ({ github_login: login, display_name: null });
const room = (id: number, unread: number, latest: number | null, muted = false) => ({
  repository: { id, name: `repo${id}` }, muted, unread_chat: unread,
  latest: latest ? { id: latest, author: person('alice'), preview: '회의 언제 할까요?', created_at: null } : null,
});
const invite = (id: number) => ({ event_id: id, repository: { id: 1, name: 'repo1' }, title: `회의 ${id}`, starts_at: '2026-11-13T11:00:00+00:00',
  all_day: false, utc_offset: 540, author: person('minji') });

function harness(responses: NotificationSummary[], options: { viewing?: number; account?: () => number | null } = {}) {
  const shown: Toast[] = []; const requests: Record<string, unknown>[] = []; const published: unknown[] = [];
  const notifier = new CommunityNotifier({
    request: async value => {
      requests.push(value as Record<string, unknown>);
      if ((value as { endpointKey: string }).endpointKey === 'communityInvitesSeen') return { status: 200, body: { marked: 1 }, headers: {} };
      return { status: 200, body: responses.shift(), headers: {} };
    },
    account: options.account ?? (() => 1),
    show: toast => shown.push(toast),
    viewingChat: id => id === options.viewing,
    publish: summary => published.push(summary),
  }, manual);
  notifier.start(0);
  return { notifier, shown, requests, published };
}

test('the endpoints main and the chat screen call are on the allowlist with request bodies', () => {
  assert.deepEqual([endpoints.communityNotifications.method, endpoints.communityNotifications.template], ['GET', '/api/community/v1/notifications']);
  assert.equal(endpoints.communityInvitesSeen.body, 'InvitesSeenRequest');
  assert.equal(endpoints.markCommunityChatRead.template, '/api/community/v1/repositories/{repositoryId}/chat/read');
  assert.equal(endpoints.communityNotificationSettings.body, 'NotificationSettingsRequest');
});

test('new chat is announced once per new latest message, grouped, and never for muted or open chats', async () => {
  const h = harness([
    { communities: [room(1, 2, 10), room(2, 1, 20, true), room(3, 1, 30)], invites: [], unread_total: 3 },
    { communities: [room(1, 2, 10), room(3, 1, 30)], invites: [], unread_total: 3 },
    { communities: [room(1, 3, 11), room(3, 2, 31)], invites: [], unread_total: 5 },
  ], { viewing: 3 });
  await h.notifier.poll();
  assert.deepEqual(h.shown.map(t => [t.title, t.body, t.tab]), [['repo1 커뮤니티 · 새 메시지', 'alice: 회의 언제 할까요? 외 1개', 'chat']]);
  await h.notifier.poll();
  assert.equal(h.shown.length, 1, 'nothing new, nothing shown');
  await h.notifier.poll();
  assert.deepEqual(h.shown.map(t => t.repositoryId), [1, 1], 'repo 3 is on screen');
  assert.deepEqual(h.published.at(-1), { communities: [room(1, 3, 11), room(3, 2, 31)], invites: [], unread_total: 5 });
});

test('invitations are announced once, at most three plus a summary, then marked seen', async () => {
  const h = harness([
    { communities: [], invites: [invite(1), invite(2), invite(3), invite(4), invite(5)], unread_total: 5 },
    { communities: [], invites: [invite(5)], unread_total: 1 },  // Seen request lost for one event.
  ]);
  await h.notifier.poll();
  assert.deepEqual(h.shown.map(t => t.body), [
    "minji 님이 '회의 1'(11월 13일 (금) 20:00)에 참여자로 추가했어요.", "minji 님이 '회의 2'(11월 13일 (금) 20:00)에 참여자로 추가했어요.",
    "minji 님이 '회의 3'(11월 13일 (금) 20:00)에 참여자로 추가했어요.", '새 일정 초대가 2개 더 있어요.']);
  assert.deepEqual(h.requests[1], { endpointKey: 'communityInvitesSeen', path: {}, query: {}, body: { event_ids: [1, 2, 3, 4, 5] } });
  await h.notifier.poll();
  assert.equal(h.shown.length, 4, 'already announced in this session');
});

test('signed out polls nothing; another account starts over', async () => {
  let account: number | null = null;
  const h = harness([{ communities: [room(1, 1, 10)], invites: [], unread_total: 1 }, { communities: [room(1, 1, 10)], invites: [], unread_total: 1 }],
    { account: () => account });
  await h.notifier.poll();
  assert.deepEqual([h.requests.length, h.published], [0, [null]]);
  account = 1; await h.notifier.poll();
  account = 2; await h.notifier.poll();
  assert.equal(h.shown.length, 2);
});

test('malformed summaries are ignored and helpers stay strict', () => {
  assert.equal(parseSummary(null), null);
  assert.equal(parseSummary({ communities: 'x', invites: [] }), null);
  const parsed = parseSummary({ communities: [room(1, 1, 5), { repository: { id: 0, name: 'x' } }], invites: [invite(1), { event_id: 2 }], unread_total: -3 });
  assert.deepEqual([parsed?.communities.length, parsed?.invites.length, parsed?.unread_total], [1, 1, 0]);
  assert.equal(chatToast(room(1, 0, null)), null);
  assert.equal(inviteToasts([]).length, 0);
  assert.equal(whenText({ starts_at: '2026-11-12T15:00:00Z', all_day: true, utc_offset: 540 }), '11월 13일 (금) 종일');
  assert.equal(communityUrl(7, 'schedule'), 'gitviz://app/web/pages/community.html?id=7&tab=schedule');
  assert.throws(() => communityUrl(0, 'chat'));
  assert.ok(showsChat('gitviz://app/web/pages/community.html?id=7&tab=chat', 7));
  assert.ok(!showsChat('gitviz://app/web/pages/community.html?id=7&tab=posts', 7));
  assert.ok(!showsChat('https://evil.example/web/pages/community.html?id=7&tab=chat', 7));
  assert.deepEqual(badgeOf({ communities: [room(1, 2, 9, true)], invites: [invite(1)], unread_total: 1 }),
    { unreadTotal: 1, communities: [{ repositoryId: 1, unreadChat: 2, muted: true }] });
});
