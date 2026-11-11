/**
 * Community notifications (2026-11-11). Main polls one server summary for the signed-in account, whatever screen is open,
 * and turns new chat messages and schedule invitations into Windows notifications. The renderer only gets the summary
 * (for the sidebar badge); the account token never leaves main.
 *
 * Chat is announced once per new latest message of a community, grouped ("외 N개"), and not while that community's chat
 * is on screen; muted communities stay quiet. Invitations are announced once and then marked seen on the server.
 * No message text is logged.
 */
import type { RemoteResponse } from './remote';

export interface EventTimer { set(action: () => void, delay: number): unknown; clear(handle: unknown): void }
const timers: EventTimer = { set: (action, delay) => setTimeout(action, delay), clear: handle => clearTimeout(handle as NodeJS.Timeout) };

interface Person { github_login: string | null; display_name: string | null }
export interface CommunitySummary {
  repository: { id: number; name: string };
  muted: boolean;
  unread_chat: number;
  latest: { id: number; author: Person | null; preview: string; created_at: string | null } | null;
}
export interface InviteSummary {
  event_id: number; repository: { id: number; name: string }; title: string; starts_at: string; all_day: boolean;
  utc_offset: number; author: Person | null;
}
export interface NotificationSummary { communities: CommunitySummary[]; invites: InviteSummary[]; unread_total: number }
export interface Toast { title: string; body: string; repositoryId: number; tab: 'chat' | 'schedule' }

export interface NotifierDeps {
  request(value: unknown): Promise<RemoteResponse>;
  /** Changes whenever the account changes (sign-in, sign-out, another account); null while signed out. */
  account(): number | null;
  show(toast: Toast): void;
  /** True while the window is focused on this community's chat tab. */
  viewingChat(repositoryId: number): boolean;
  publish(summary: NotificationSummary | null): void;
}

export const POLL_INTERVAL = 30000;
export const INVITE_TOASTS = 3;

const name = (person: Person | null): string => person?.display_name || person?.github_login || '알 수 없는 사용자';
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) > 0;

/** Keeps only well-formed entries; anything else from the server is ignored rather than shown. */
export function parseSummary(value: unknown): NotificationSummary | null {
  if (!isRecord(value) || !Array.isArray(value.communities) || !Array.isArray(value.invites)) return null;
  const repo = (item: unknown) => isRecord(item) && positive(item.id) && typeof item.name === 'string';
  const communities = value.communities.filter((item): item is CommunitySummary => isRecord(item) && repo(item.repository)
    && typeof item.muted === 'boolean' && Number.isSafeInteger(item.unread_chat) && (item.unread_chat as number) >= 0
    && (item.latest === null || (isRecord(item.latest) && positive(item.latest.id) && typeof item.latest.preview === 'string')));
  const invites = value.invites.filter((item): item is InviteSummary => isRecord(item) && positive(item.event_id) && repo(item.repository)
    && typeof item.title === 'string' && typeof item.starts_at === 'string' && Number.isFinite(Date.parse(item.starts_at)));
  const total = Number.isSafeInteger(value.unread_total) ? value.unread_total as number : 0;
  return { communities, invites, unread_total: Math.max(0, total) };
}

/** Start time in the offset the event was entered with, e.g. "11월 13일 (목) 20:00". */
export function whenText(invite: Pick<InviteSummary, 'starts_at' | 'all_day' | 'utc_offset'>): string {
  const offset = Number.isSafeInteger(invite.utc_offset) ? invite.utc_offset : 0;
  const local = new Date(Date.parse(invite.starts_at) + offset * 60000);
  const day = `${local.getUTCMonth() + 1}월 ${local.getUTCDate()}일 (${'일월화수목금토'[local.getUTCDay()]})`;
  if (invite.all_day) return `${day} 종일`;
  return `${day} ${String(local.getUTCHours()).padStart(2, '0')}:${String(local.getUTCMinutes()).padStart(2, '0')}`;
}

export function chatToast(item: CommunitySummary): Toast | null {
  if (!item.latest || item.unread_chat < 1) return null;
  const more = item.unread_chat > 1 ? ` 외 ${item.unread_chat - 1}개` : '';
  return { title: `${item.repository.name} 커뮤니티 · 새 메시지`, body: `${name(item.latest.author)}: ${item.latest.preview}${more}`,
    repositoryId: item.repository.id, tab: 'chat' };
}

export function inviteToasts(invites: InviteSummary[]): Toast[] {
  const shown = invites.slice(0, INVITE_TOASTS).map(invite => ({
    title: `${invite.repository.name} 커뮤니티 · 일정 초대`,
    body: `${name(invite.author)} 님이 '${invite.title}'(${whenText(invite)})에 참여자로 추가했어요.`,
    repositoryId: invite.repository.id, tab: 'schedule' as const,
  }));
  const rest = invites.length - shown.length;
  if (rest > 0) shown.push({ title: '일정 초대', body: `새 일정 초대가 ${rest}개 더 있어요.`, repositoryId: invites[INVITE_TOASTS].repository.id, tab: 'schedule' });
  return shown;
}

export class CommunityNotifier {
  private handle: unknown = null;
  private polling = false;
  private stopped = true;
  private owner: number | null = null;
  private readonly announced = new Map<number, number>();  // repository id -> latest chat message id already handled
  private readonly invited = new Set<number>();             // event ids announced in this session (in case "seen" fails)
  constructor(private readonly deps: NotifierDeps, private readonly timer: EventTimer = timers, private readonly interval = POLL_INTERVAL) {}

  start(delay = 3000): void { this.stopped = false; this.schedule(delay); }
  stop(): void { this.stopped = true; if (this.handle !== null) this.timer.clear(this.handle); this.handle = null; }
  /** Poll soon (the chat screen moved the read position, or the account changed). */
  refresh(): void { if (!this.stopped) { if (this.handle !== null) this.timer.clear(this.handle); this.schedule(300); } }

  private schedule(delay = this.interval): void { if (!this.stopped) this.handle = this.timer.set(() => { void this.poll(); }, delay); }

  async poll(): Promise<void> {
    this.handle = null;
    if (this.polling || this.stopped) return;
    this.polling = true;
    try {
      const account = this.deps.account();
      if (account !== this.owner) { this.owner = account; this.announced.clear(); this.invited.clear(); }
      if (account === null) { this.deps.publish(null); return; }
      const response = await this.deps.request({ endpointKey: 'communityNotifications', path: {}, query: {} });
      if (this.deps.account() !== account) return;  // Account changed while waiting; the next tick starts over.
      const summary = response.status === 200 ? parseSummary(response.body) : null;
      if (!summary) return;
      this.deps.publish(summary);
      for (const item of summary.communities) {
        const latest = item.latest?.id ?? 0;
        const before = this.announced.get(item.repository.id) ?? 0;
        this.announced.set(item.repository.id, Math.max(before, latest));
        if (item.muted || latest <= before || this.deps.viewingChat(item.repository.id)) continue;
        const toast = chatToast(item);
        if (toast) this.deps.show(toast);
      }
      const fresh = summary.invites.filter(invite => !this.invited.has(invite.event_id));
      for (const toast of inviteToasts(fresh)) this.deps.show(toast);
      for (const invite of fresh) this.invited.add(invite.event_id);
      if (summary.invites.length) {
        await this.deps.request({ endpointKey: 'communityInvitesSeen', path: {}, query: {},
          body: { event_ids: summary.invites.map(invite => invite.event_id).slice(0, 50) } }).catch(() => undefined);
      }
    } catch { /* Offline or signed out: keep polling; the next tick retries. */ }
    finally { this.polling = false; if (this.handle === null) this.schedule(); }
  }
}

const COMMUNITY_PAGE = 'gitviz://app/web/pages/community.html';
export function communityUrl(repositoryId: number, tab: Toast['tab']): string {
  if (!positive(repositoryId)) throw new Error('INVALID_REQUEST');
  return `${COMMUNITY_PAGE}?id=${repositoryId}&tab=${tab}`;
}
/** Whether this app URL shows the given community's chat tab. */
export function showsChat(raw: string, repositoryId: number): boolean {
  try {
    const url = new URL(raw);
    return `${url.protocol}//${url.host}${url.pathname}` === COMMUNITY_PAGE && url.searchParams.get('id') === String(repositoryId)
      && url.searchParams.get('tab') === 'chat';
  } catch { return false; }
}
/** What the renderer gets for the sidebar badge: counts only, no message text. */
export function badgeOf(summary: NotificationSummary | null): { unreadTotal: number; communities: { repositoryId: number; unreadChat: number; muted: boolean }[] } | null {
  return summary ? { unreadTotal: summary.unread_total, communities: summary.communities.map(item => ({
    repositoryId: item.repository.id, unreadChat: item.unread_chat, muted: item.muted })) } : null;
}
