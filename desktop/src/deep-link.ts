import { basename, dirname, join } from 'node:path';

/**
 * External links into the app (AD-26). The OS hands us `gitviz-join://invite/<token>` on the command line.
 * The scheme carries community invite tokens only, never a page or path, so a web page cannot steer the app
 * anywhere else; joining still needs the user's own click in the community page.
 */
export const JOIN_SCHEME = 'gitviz-join';
const TOKEN = /^[A-Za-z0-9_-]{43}$/;

export function inviteFromUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 128) return null;
  let url: URL;
  try { url = new URL(value); } catch { return null; }
  if (url.protocol !== `${JOIN_SCHEME}:` || url.hostname !== 'invite' || url.username || url.password || url.port || url.search || url.hash) return null;
  const token = url.pathname.replace(/^\/|\/$/g, '');
  return TOKEN.test(token) ? token : null;
}

export function inviteFromArgv(argv: readonly string[]): string | null {
  for (const arg of argv) if (arg.toLowerCase().startsWith(`${JOIN_SCHEME}:`)) return inviteFromUrl(arg);
  return null;
}

export function communityJoinUrl(token: string): string {
  if (!TOKEN.test(token)) throw new Error('INVALID_INVITE');
  return `gitviz://app/web/pages/community.html?invite=${token}`;
}

/**
 * Packaged per-user installs only. Squirrel keeps the exe in a versioned app-x.y.z folder, so the protocol points at
 * the stable launcher one level up (it forwards arguments to the current version). `--` ends Chromium switch parsing,
 * so nothing in a link can become a command-line switch.
 */
export function protocolRegistration(packaged: boolean, platform: string, execPath: string): { scheme: string; path: string; args: string[] } | null {
  if (!packaged || platform !== 'win32' || !/^app-\d+\.\d+\.\d+/i.test(basename(dirname(execPath)))) return null;
  return { scheme: JOIN_SCHEME, path: join(dirname(dirname(execPath)), basename(execPath)), args: ['--'] };
}
