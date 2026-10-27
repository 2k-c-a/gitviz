import { clipboard, dialog, ipcMain, type BrowserWindow } from 'electron';
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { lstat, readFile, realpath, rename, writeFile } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, relative, sep } from 'node:path';
import { Agent } from './agent';
import { Contracts } from './contracts';
import { trustedSender } from './ipc';

type EditorId = 'vscode' | 'cursor';
const binaries = { vscode: 'code.exe', cursor: 'cursor.exe' } as const;
export function editorArguments(path: string, line: number, column: number): string[] {
  if (!isAbsolute(path) || (path.startsWith('\\\\') || path.startsWith('//')) || Array.from(path).some(char => char.charCodeAt(0) < 32) ||
      !Number.isInteger(line) || line < 1 || line > 1000000 || !Number.isInteger(column) || column < 1 || column > 1000000) throw new Error('INVALID_REQUEST');
  return ['--reuse-window', '--goto', `${path}:${line}:${column}`];
}
async function checked(path: string, root?: string): Promise<string> {
  if (!isAbsolute(path) || (path.startsWith('\\\\') || path.startsWith('//'))) throw new Error('PATH_OUTSIDE_SCOPE');
  for (let current = path; ; current = dirname(current)) {
    if ((await lstat(current)).isSymbolicLink()) throw new Error('PATH_OUTSIDE_SCOPE');
    if (dirname(current) === current) break;
  }
  const resolved = await realpath(path);
  if (root) {
    const part = relative(await realpath(root), resolved);
    if (!part || isAbsolute(part) || part === '..' || part.startsWith('..' + sep)) throw new Error('PATH_OUTSIDE_SCOPE');
  }
  if (!(await lstat(resolved)).isFile()) throw new Error('RESOURCE_NOT_FOUND');
  return resolved;
}
export async function verifyEditorBinary(path: string, editorId: EditorId): Promise<{ publisher: string; version: string }> {
  const executable = await checked(path);
  if (basename(executable).toLowerCase() !== binaries[editorId] || (await lstat(executable)).size > 300000000) throw new Error('COMMAND_NOT_ALLOWED');
  const systemRoot = process.env.SystemRoot ?? 'C:\\Windows';
  const powershell = join(systemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
  const env: NodeJS.ProcessEnv = {};
  for (const key of ['SystemRoot', 'WINDIR', 'TEMP', 'TMP']) if (process.env[key]) env[key] = process.env[key];
  env.PSModulePath = join(systemRoot, 'System32/WindowsPowerShell/v1.0/Modules');
  // The selected path is stdin data; it is never interpolated into shell code.
  const script = "$p=[Console]::In.ReadToEnd();$s=Microsoft.PowerShell.Security\\Get-AuthenticodeSignature -LiteralPath $p;$v=[Diagnostics.FileVersionInfo]::GetVersionInfo($p);[pscustomobject]@{status=[string]$s.Status;publisher=$s.SignerCertificate.Subject;product=$v.ProductName;version=$v.ProductVersion}|ConvertTo-Json -Compress";
  const result = await new Promise<{ status: string; publisher: string; product: string; version: string }>((resolve, reject) => {
    const child = spawn(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script], { env, shell: false, windowsHide: true, stdio: 'pipe', cwd: systemRoot });
    let output = ''; let bytes = 0; let done = false;
    const timer = setTimeout(() => finish(), 8000);
    function finish(value?: { status: string; publisher: string; product: string; version: string }) {
      if (done) return; done = true; clearTimeout(timer); child.kill();
      if (value) resolve(value); else reject(new Error('EDITOR_SIGNATURE_UNAVAILABLE'));
    }
    child.once('error', () => finish()); child.stdin.on('error', () => finish());
    child.stdout.on('data', (data: Buffer) => { bytes += data.length; if (bytes > 8192) finish(); else output += data.toString('utf8'); });
    child.stderr.on('data', (data: Buffer) => { bytes += data.length; if (bytes > 8192) finish(); });
    child.once('close', code => {
      if (code !== 0) { finish(); return; }
      try { finish(JSON.parse(output) as { status: string; publisher: string; product: string; version: string }); } catch { finish(); }
    });
    child.stdin.end(executable);
  });
  const publisher = editorId === 'vscode' ? /O=Microsoft Corporation(?:,|$)/ : /O="?Anysphere, Inc\."?(?:,|$)/;
  const products = editorId === 'vscode' ? ['Visual Studio Code', 'Microsoft Visual Studio Code'] : ['Cursor'];
  if (result.status !== 'Valid' || !publisher.test(result.publisher) || !products.includes(result.product)) throw new Error('COMMAND_NOT_ALLOWED');
  return { publisher: result.publisher, version: result.version };
}
export function registerEditor(window: BrowserWindow, agent: Pick<Agent, 'call'>, contracts: Contracts, directory: string): () => void {
  const channels = ['workspace:configure-editor', 'workspace:open-editor', 'workspace:copy-location'];
  let configuring = false;
  ipcMain.handle(channels[0]!, async (event, value: unknown) => {
    if (!trustedSender(event, window)) throw new Error('ACCESS_DENIED');
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== 1 ||
        !('editorId' in value) || !['vscode', 'cursor'].includes(String(value.editorId))) throw new Error('INVALID_REQUEST');
    if (configuring) throw new Error('BUSY');
    configuring = true;
    try {
      const editorId = value.editorId as EditorId;
      const result = await dialog.showOpenDialog(window, { title: `${editorId} 실행 파일 선택`, properties: ['openFile'], filters: [{ name: 'Editor', extensions: ['exe'] }] });
      if (result.canceled || result.filePaths.length !== 1) return null;
      const executable = await checked(result.filePaths[0]!);
      if (basename(executable).toLowerCase() !== binaries[editorId]) throw new Error('COMMAND_NOT_ALLOWED');
      const signature = await verifyEditorBinary(executable, editorId);
      const data = await readFile(executable);
      if (data.length < 64 || data.subarray(0, 2).toString() !== 'MZ') throw new Error('COMMAND_NOT_ALLOWED');
      if ((await dialog.showMessageBox(window, { type: 'question', buttons: ['취소', '이 실행 파일 사용'], defaultId: 0, cancelId: 0,
        message: `${editorId} 파일·줄 이동`, detail: `${executable}\n${signature.publisher}\n${signature.version}` })).response !== 1) return null;
      const record = { editorId, executable, hash: createHash('sha256').update(data).digest('hex'), ...signature };
      const pending = join(directory, `editor-${randomUUID()}.pending`);
      await writeFile(pending, JSON.stringify(record), { flag: 'wx' });
      await rename(pending, join(directory, `editor-${editorId}.json`));
      return { editorId, configured: true };
    } catch {
      throw new Error('EDITOR_CONFIGURATION_FAILED');
    } finally { configuring = false; }
  });
  ipcMain.handle(channels[1]!, async (event, value: unknown) => {
    if (!trustedSender(event, window)) throw new Error('ACCESS_DENIED');
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_REQUEST');
    const { editorId, projectId, ...payload } = value as Record<string, unknown>;
    if (editorId !== 'vscode' && editorId !== 'cursor') throw new Error('INVALID_REQUEST');
    contracts.request({ protocolVersion: 1, requestId: randomUUID(), operation: 'workspace.files.locate', projectId, payload });
    try {
      const record = JSON.parse(await readFile(join(directory, `editor-${editorId}.json`), 'utf8')) as { executable: string; hash: string; editorId: string };
      const executable = await checked(record.executable);
      if (record.editorId !== editorId || basename(executable).toLowerCase() !== binaries[editorId] ||
          createHash('sha256').update(await readFile(executable)).digest('hex') !== record.hash) throw new Error('PRECONDITION_FAILED');
      const location = await agent.call('workspace.files.locate', payload, { projectId: projectId as string });
      const path = await checked(String(location.absolutePath), String(location.root));
      if (createHash('sha256').update(await readFile(path)).digest('hex') !== location.sourceHash) throw new Error('PRECONDITION_FAILED');
      const argv = editorArguments(path, Number(location.line), Number(location.column));
      const env: NodeJS.ProcessEnv = {};
      for (const name of ['SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA']) if (process.env[name]) env[name] = process.env[name];
      await new Promise<void>((resolve, reject) => {
        const child = spawn(executable, argv, { shell: false, windowsHide: true, detached: false, stdio: 'ignore', cwd: String(location.root), env });
        child.once('error', reject); child.once('spawn', () => { child.unref(); resolve(); });
      });
      return { editorId, launched: true, limitations: ['live-editor-file-may-change-after-launch', 'launch-is-not-edit-completion'] };
    } catch {
      throw new Error('EDITOR_UNAVAILABLE_OR_STALE');
    }
  });
  ipcMain.handle(channels[2]!, async (event, value: unknown) => {
    if (!trustedSender(event, window)) throw new Error('ACCESS_DENIED');
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_REQUEST');
    const { projectId, ...payload } = value as Record<string, unknown>;
    const request = { protocolVersion: 1, requestId: randomUUID(), operation: 'workspace.files.locate', projectId, payload };
    contracts.request(request);
    const location = await agent.call('workspace.files.locate', payload, { projectId: request.projectId });
    clipboard.writeText(`${String(location.absolutePath)}:${String(location.line)}:${String(location.column)}`);
    return { copied: true };
  });
  return () => { for (const channel of channels) ipcMain.removeHandler(channel); };
}
