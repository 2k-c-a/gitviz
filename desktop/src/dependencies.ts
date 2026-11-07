// System environment check and per-user installation of the one external program the app needs (Git for Windows, K-12).
// Electron, the frozen agent (with its Python runtime and VC++/UCRT DLLs) are inside the installer, so they are only
// checked for presence. No path, account or download URL with credentials is ever returned to the renderer or logged.
import { createHash, randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, open, rm } from 'node:fs/promises';
import { release } from 'node:os';
import { join } from 'node:path';

export type DependencyStatus = 'ok' | 'missing' | 'outdated' | 'unsupported';
export type DependencyAction = 'none' | 'install' | 'manual';
export interface DependencyItem {
  id: 'os' | 'arch' | 'runtime' | 'agent' | 'git';
  name: string;
  required: boolean;
  status: DependencyStatus;
  action: DependencyAction;
  version: string | null;
  expected: string;
  note: string;
}
export interface DependencyReport { ready: boolean; items: DependencyItem[] }
export interface InstallProgress { id: 'git'; stage: 'download' | 'verify' | 'install' | 'check'; received?: number; total?: number }

// Oldest Git the app accepts. The agent and the server-side collectors use only long-standing plumbing
// (log --numstat, ls-tree, rev-list, cat-file, -c overrides); 2.30 is the floor this release supports.
export const GIT_MINIMUM = '2.30.0';
// Pinned official release (github.com/git-for-windows). The SHA-256 was taken from the GitHub release asset digest and
// matches the hash table in the release notes; the download is rejected unless it matches byte for byte.
export const GIT_PACKAGE = Object.freeze({
  version: '2.56.0.windows.4',
  url: 'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/Git-2.56.0.4-64-bit.exe',
  size: 66_850_032,
  sha256: '4961a4a8cc778d8b6e00a5819e5fe4d078b754555aff7aedc2d77a5d74c2de80',
});
export const GIT_DOWNLOAD_PAGE = 'https://git-scm.com/downloads/win';
const WINDOWS_10_BUILD = 10240;
const WINDOWS_11_BUILD = 22000;

/** Same two locations the agent searches (app/workspace/git_service.py installed_git), in the same order. */
export function gitCandidates(env: NodeJS.ProcessEnv): string[] {
  return [join(env.ProgramFiles ?? 'C:/Program Files', 'Git', 'cmd', 'git.exe'),
    ...(env.LOCALAPPDATA ? [join(env.LOCALAPPDATA, 'Programs', 'Git', 'cmd', 'git.exe')] : [])];
}

/** Per-user install directory: no administrator rights, and the second location gitCandidates() looks at. */
export function gitUserDirectory(env: NodeJS.ProcessEnv): string | null {
  return env.LOCALAPPDATA ? join(env.LOCALAPPDATA, 'Programs', 'Git') : null;
}

export function parseGitVersion(output: string): string | null {
  const match = /git version (\d+)\.(\d+)\.(\d+)/.exec(output);
  return match ? `${match[1]}.${match[2]}.${match[3]}` : null;
}

export function compareVersions(left: string, right: string): number {
  const a = left.split('.').map(Number), b = right.split('.').map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const delta = (a[i] ?? 0) - (b[i] ?? 0);
    if (delta) return delta < 0 ? -1 : 1;
  }
  return 0;
}

export interface CheckEnvironment {
  platform: NodeJS.Platform;
  arch: string;
  osRelease: string;
  env: NodeJS.ProcessEnv;
  exists: (path: string) => boolean;
  runGit: (executable: string) => Promise<string | null>;
  agentExecutable: string | null;
}

function runGitVersion(executable: string): Promise<string | null> {
  return new Promise(resolve => {
    const env: NodeJS.ProcessEnv = { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: 'NUL' };
    for (const name of ['SystemRoot', 'PATH', 'TEMP', 'TMP']) if (process.env[name]) env[name] = process.env[name];
    execFile(executable, ['--version'], { timeout: 10_000, windowsHide: true, env },
      (error, stdout) => resolve(error ? null : String(stdout)));
  });
}

export function defaultCheckEnvironment(agentExecutable: string | null): CheckEnvironment {
  return { platform: process.platform, arch: process.arch, osRelease: release(), env: process.env, exists: existsSync, runGit: runGitVersion, agentExecutable };
}

export async function checkDependencies(input: CheckEnvironment): Promise<DependencyReport> {
  const items: DependencyItem[] = [];
  const build = Number(input.osRelease.split('.')[2] ?? 0);
  const windows = input.platform === 'win32' && input.osRelease.startsWith('10.') && build >= WINDOWS_10_BUILD;
  items.push({ id: 'os', name: '운영체제', required: true, status: windows ? 'ok' : 'unsupported', action: windows ? 'none' : 'manual',
    version: input.platform === 'win32' ? `${build >= WINDOWS_11_BUILD ? 'Windows 11' : 'Windows 10'} (빌드 ${build})` : input.platform,
    expected: 'Windows 10 또는 Windows 11', note: windows ? '' : '이 앱은 Windows 10·11에서만 실행됩니다.' });
  // An x64 build also runs under emulation on Windows on ARM; the native architecture is reported for support only.
  const native = (input.env.PROCESSOR_ARCHITEW6432 ?? input.env.PROCESSOR_ARCHITECTURE ?? input.arch).toLowerCase();
  const x64 = input.arch === 'x64';
  items.push({ id: 'arch', name: '시스템 아키텍처', required: true, status: x64 ? 'ok' : 'unsupported', action: x64 ? 'none' : 'manual',
    version: native === 'amd64' || native === 'x64' ? 'x64' : native === 'arm64' ? 'ARM64 (x64 에뮬레이션)' : native, expected: 'x64',
    note: x64 ? '' : '64비트(x64) Windows가 필요합니다.' });
  items.push({ id: 'runtime', name: '앱 런타임 (Electron·Node.js)', required: true, status: 'ok', action: 'none',
    version: process.versions.electron ?? null, expected: '설치 파일에 포함', note: '따로 설치할 필요가 없습니다.' });
  const agent = input.agentExecutable !== null && input.exists(input.agentExecutable);
  items.push({ id: 'agent', name: '로컬 작업 엔진 (Python 런타임·VC++ 포함)', required: true, status: agent ? 'ok' : 'missing', action: agent ? 'none' : 'manual',
    version: null, expected: '설치 파일에 포함', note: agent ? '따로 설치할 필요가 없습니다.' : '설치가 손상되었습니다. 설치 파일을 다시 실행해 주세요.' });
  const found = gitCandidates(input.env).find(path => input.exists(path));
  const version = found ? parseGitVersion((await input.runGit(found)) ?? '') : null;
  const gitStatus: DependencyStatus = !found ? 'missing' : version && compareVersions(version, GIT_MINIMUM) >= 0 ? 'ok' : 'outdated';
  items.push({ id: 'git', name: 'Git for Windows', required: true, status: gitStatus, action: gitStatus === 'ok' ? 'none' : 'install', version,
    expected: `${GIT_MINIMUM} 이상`, note: gitStatus === 'ok' ? '' : gitStatus === 'missing'
      ? '로컬 프로젝트를 읽으려면 필요합니다. 공식 배포본을 내 계정에만 설치합니다(관리자 권한 없음).'
      : `설치된 버전을 확인할 수 없거나 ${GIT_MINIMUM}보다 낮습니다. 새 버전을 내 계정에 설치합니다.` });
  return { ready: items.every(item => !item.required || item.status === 'ok'), items };
}

export class DependencyError extends Error {
  constructor(readonly code: 'NO_USER_DIRECTORY' | 'DOWNLOAD_FAILED' | 'INTEGRITY_FAILED' | 'INSTALL_FAILED' | 'INSTALL_TIMEOUT' | 'NOT_DETECTED' | 'BUSY') { super(code); }
}

export interface InstallerEnvironment {
  env: NodeJS.ProcessEnv;
  tempDirectory: string;
  fetch: (url: string) => Promise<Response>;
  run: (executable: string, args: string[]) => Promise<number>;
  check: () => Promise<DependencyReport>;
}

function runInstaller(executable: string, args: string[]): Promise<number> {
  return new Promise((resolve, reject) => {
    // Unelevated: Git's installer runs with the user's own rights (PrivilegesRequired=none) and writes to HKCU.
    execFile(executable, args, { timeout: 15 * 60_000, windowsHide: true }, error => {
      if (!error) { resolve(0); return; }
      const failure = error as NodeJS.ErrnoException & { killed?: boolean; code?: number | string };
      if (failure.killed) reject(new DependencyError('INSTALL_TIMEOUT'));
      else resolve(typeof failure.code === 'number' ? failure.code : 1);
    });
  });
}

export function defaultInstallerEnvironment(tempDirectory: string, fetchImpl: (url: string) => Promise<Response>, check: () => Promise<DependencyReport>): InstallerEnvironment {
  return { env: process.env, tempDirectory, fetch: fetchImpl, run: runInstaller, check };
}

/** Download the pinned Git installer, verify size and SHA-256, run it silently for the current user, then check again. */
export async function installGit(input: InstallerEnvironment, progress: (value: InstallProgress) => void): Promise<DependencyReport> {
  const target = gitUserDirectory(input.env);
  if (!target) throw new DependencyError('NO_USER_DIRECTORY');
  const directory = join(input.tempDirectory, 'GitViz');
  await mkdir(directory, { recursive: true });
  const file = join(directory, `git-setup-${randomUUID()}.exe`);
  try {
    progress({ id: 'git', stage: 'download', received: 0, total: GIT_PACKAGE.size });
    let response: Response;
    try { response = await input.fetch(GIT_PACKAGE.url); } catch { throw new DependencyError('DOWNLOAD_FAILED'); }
    if (!response.ok || !response.body) throw new DependencyError('DOWNLOAD_FAILED');
    const hash = createHash('sha256');
    const handle = await open(file, 'wx', 0o600);
    let received = 0, last = 0;
    try {
      const reader = response.body.getReader();
      for (;;) {
        let chunk: ReadableStreamReadResult<Uint8Array>;
        try { chunk = await reader.read(); } catch { throw new DependencyError('DOWNLOAD_FAILED'); }
        if (chunk.done) break;
        received += chunk.value.byteLength;
        if (received > GIT_PACKAGE.size) throw new DependencyError('INTEGRITY_FAILED');
        hash.update(chunk.value);
        await handle.write(chunk.value);
        if (received - last >= 512 * 1024) { last = received; progress({ id: 'git', stage: 'download', received, total: GIT_PACKAGE.size }); }
      }
      await handle.sync();
    } finally { await handle.close(); }
    progress({ id: 'git', stage: 'verify', received, total: GIT_PACKAGE.size });
    if (received !== GIT_PACKAGE.size || hash.digest('hex') !== GIT_PACKAGE.sha256) throw new DependencyError('INTEGRITY_FAILED');
    progress({ id: 'git', stage: 'install' });
    const code = await input.run(file, ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCANCEL', '/SP-', `/DIR=${target}`]);
    if (code !== 0) throw new DependencyError('INSTALL_FAILED');
    progress({ id: 'git', stage: 'check' });
    const report = await input.check();
    if (report.items.find(item => item.id === 'git')?.status !== 'ok') throw new DependencyError('NOT_DETECTED');
    return report;
  } finally {
    await rm(file, { force: true }).catch(() => {});
  }
}
