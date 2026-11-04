// Co-editing file sets (AD-34). The host picks a start file; main follows its references (HTML/CSS/JS) inside a root
// folder and offers that set for co-editing. Absolute paths stay here: the page gets an opaque bundle id and paths
// relative to the root. New references that appear while people edit are read only after the host approves them.
// The host can also add a file that does not exist yet; it is created on disk only when the host saves.
import { randomUUID } from 'node:crypto';
import { realpath as realpathCallback } from 'node:fs';
import { lstat, mkdir, readFile, stat } from 'node:fs/promises';
import { promisify } from 'node:util';
import { homedir } from 'node:os';
import { basename, dirname, extname, isAbsolute, join, parse, posix, relative, sep } from 'node:path';
import { analyze, refKind, type Analysis } from './collab-refs';
import { LANGUAGES, MAX_FILE_BYTES, digest, readText, writeText } from './collab-files';

export const LIMITS = { textFiles: 20, textBytes: 1024 * 1024, binaryFile: 2 * 1024 * 1024, binaryFiles: 30, binaryBytes: 10 * 1024 * 1024 };
const MAX_BUNDLES = 4;
const MAX_SCAN_TEXT = 400 * 1024;
const REPO_FREE_LEVELS = 3;

const TEXT_EXTENSIONS = new Set([...Object.keys(LANGUAGES), '.txt', '.svg']);
const languageOf = (path: string) => LANGUAGES[extname(path).toLowerCase()] ?? (extname(path).toLowerCase() === '.svg' ? 'html' : 'plain');
const starts = (bytes: Uint8Array, at: number, text: string) => [...text].every((c, i) => bytes[at + i] === c.charCodeAt(0));
const BINARY: Record<string, { mime: string; check: (b: Uint8Array) => boolean }> = {
  '.png': { mime: 'image/png', check: b => starts(b, 0, '\x89PNG\r\n\x1a\n') },
  '.jpg': { mime: 'image/jpeg', check: b => b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  '.jpeg': { mime: 'image/jpeg', check: b => b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  '.gif': { mime: 'image/gif', check: b => starts(b, 0, 'GIF87a') || starts(b, 0, 'GIF89a') },
  '.webp': { mime: 'image/webp', check: b => starts(b, 0, 'RIFF') && starts(b, 8, 'WEBP') },
  '.avif': { mime: 'image/avif', check: b => starts(b, 4, 'ftyp') && /avi[fs]/.test(String.fromCharCode(...b.subarray(8, 40))) },
  '.ico': { mime: 'image/x-icon', check: b => b[0] === 0 && b[1] === 0 && b[2] === 1 && b[3] === 0 },
  '.bmp': { mime: 'image/bmp', check: b => starts(b, 0, 'BM') },
  '.woff2': { mime: 'font/woff2', check: b => starts(b, 0, 'wOF2') },
  '.woff': { mime: 'font/woff', check: b => starts(b, 0, 'wOFF') },
  '.ttf': { mime: 'font/ttf', check: b => (b[0] === 0 && b[1] === 1 && b[2] === 0 && b[3] === 0) || starts(b, 0, 'true') },
  '.otf': { mime: 'font/otf', check: b => starts(b, 0, 'OTTO') },
};

export type Reason = 'outside' | 'hidden' | 'link' | 'type' | 'size' | 'missing' | 'limit' | 'signature' | 'not-text';
export interface FileView { path: string; kind: 'text' | 'binary'; size: number; language?: string; text?: string; bytes?: Uint8Array; mime?: string; sha256?: string }
export interface BundleView {
  bundle: string; entry: string; root: string; canWiden: boolean; files: FileView[];
  excluded: { from: string; spec: string; reason: Reason }[]; unresolved: { from: string; line: number; spec: string }[];
  external: string[]; candidates: { from: string; path: string }[];
}
interface Entry { rel: string; abs: string; kind: 'text' | 'binary'; size: number; hash: string; crlf: boolean; bom: boolean; mime?: string; view: FileView; created?: boolean }
interface Bundle { id: string; start: string; root: string; entry: string; files: Map<string, Entry>; pending: Map<string, string>; declined: Set<string>; candidates: Map<string, string> }

// The disk's own spelling of a path (case and 8.3 names resolved) after following links and junctions.
const nativeRealpath = promisify(realpathCallback.native);
const key = (rel: string) => rel.toLowerCase();
const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();
const DEVICE = /^(?:con|prn|aux|nul|com\d|lpt\d|conin\$|conout\$)$/i;

/** Root-relative path for a reference, or null when it leaves the root or cannot name a file. `/x` means root/x. */
export function resolveRef(fromRel: string, spec: string): string | null {
  let value = spec.replace(/\\/g, '/');
  try { value = decodeURIComponent(value); } catch { /* keep the raw text */ }
  if (/^[a-zA-Z]:/.test(value) || value.startsWith('//') || value.includes('\0')) return null;
  const joined = value.startsWith('/') ? value.slice(1) : posix.join(posix.dirname(fromRel), value);
  const rel = posix.normalize(joined).replace(/^(?:\.\/)+/, '');
  if (!rel || rel === '.' || rel === '..' || rel.startsWith('../') || rel.endsWith('/') || isAbsolute(rel)) return null;
  return rel;
}

/** Hidden files and folders (.git, .env …), node_modules, device names and characters Windows cannot name. */
export function nameReason(rel: string): Reason | null {
  for (const part of rel.split('/')) {
    if (!part || part.startsWith('.') || part.toLowerCase() === 'node_modules') return 'hidden';
    if (DEVICE.test(part.split('.')[0]) || /[<>:"|?*]/.test(part) || [...part].some(c => c.charCodeAt(0) < 32) || /[. ]$/.test(part)) return 'hidden';
  }
  return null;
}

function forbiddenRoot(path: string, home: string): boolean {
  if (path === parse(path).root) return true;
  const rel = relative(path, home);
  if (rel === '' || (!rel.startsWith('..') && !isAbsolute(rel))) return true; // the home folder or one of its parents
  return same(dirname(path), home) && /^(?:desktop|documents|downloads|onedrive.*|pictures|music|videos|바탕 화면|문서|다운로드)$/i.test(basename(path));
}

async function exists(path: string): Promise<boolean> { try { await lstat(path); return true; } catch { return false; } }

/** Folders the host may widen the root to, nearest first: up to the Git repository root, else three levels; never a drive or the profile. */
export async function widerRoots(base: string, home = homedir()): Promise<string[]> {
  const ancestors: string[] = [];
  let repository: string | null = null;
  for (let current = base; ; current = dirname(current)) {
    if (await exists(join(current, '.git'))) { repository = current; break; }
    if (dirname(current) === current) break;
  }
  for (let current = base, level = 0; dirname(current) !== current;) {
    if (repository && same(current, repository)) break;
    current = dirname(current); level++;
    if (!repository && level > REPO_FREE_LEVELS) break;
    if (forbiddenRoot(current, home)) break;
    ancestors.push(current);
  }
  return ancestors;
}

export class CollabBundles {
  private readonly bundles = new Map<string, Bundle>();

  constructor(private readonly pick: (defaultPath?: string) => Promise<string | null>, private readonly confirmOverwrite: (name: string) => Promise<boolean>,
    private readonly home = homedir()) {}

  async open(): Promise<BundleView | { cancelled: true }> {
    if (this.bundles.size >= MAX_BUNDLES) throw new Error('LIMIT');
    const picked = await this.pick();
    if (!picked) return { cancelled: true };
    let start: string;
    try { start = await nativeRealpath(picked); } catch { throw new Error('INVALID_FILE'); }
    if (!TEXT_EXTENSIONS.has(extname(start).toLowerCase())) throw new Error('NOT_ALLOWED');
    await readText(start); // rejects links, large and non-text files with the existing messages
    const bundle: Bundle = { id: randomUUID(), start, root: dirname(start), entry: basename(start), files: new Map(), pending: new Map(), declined: new Set(), candidates: new Map() };
    this.bundles.set(bundle.id, bundle);
    return this.collect(bundle);
  }

  async widen(value: unknown): Promise<BundleView> {
    const bundle = this.bundle(value, ['bundle']);
    const wider = (await widerRoots(bundle.root, this.home))[0];
    if (!wider) throw new Error('CANNOT_WIDEN');
    bundle.root = wider;
    bundle.entry = relative(wider, bundle.start).split(sep).join('/');
    bundle.files.clear(); bundle.pending.clear(); bundle.candidates.clear();
    return this.collect(bundle);
  }

  /** Adds a candidate (page link, fetch literal) by path, or a file the host picks in a dialog; then follows its references. */
  async add(value: unknown): Promise<BundleView | { cancelled: true }> {
    const bundle = this.bundle(value, ['bundle', 'path']);
    const path = (value as { path?: unknown }).path;
    let rel: string;
    if (path !== undefined) {
      if (typeof path !== 'string' || !bundle.candidates.has(key(path))) throw new Error('NOT_PENDING');
      rel = bundle.candidates.get(key(path))!;
    } else {
      const picked = await this.pick(bundle.root);
      if (!picked) return { cancelled: true };
      let real: string;
      try { real = await nativeRealpath(picked); } catch { throw new Error('INVALID_FILE'); }
      const inside = relative(bundle.root, real);
      if (!inside || inside.startsWith('..') || isAbsolute(inside)) throw new Error('OUTSIDE_ROOT');
      rel = inside.split(sep).join('/');
    }
    return this.include(bundle, rel);
  }

  /** Finds references added while editing. It checks names and sizes on disk but reads nothing: the host approves first. */
  async scan(value: unknown): Promise<{ pending: string[]; missing: string[]; external: string[]; unresolved: BundleView['unresolved']; candidates: BundleView['candidates'] }> {
    const bundle = this.bundle(value, ['bundle', 'files']);
    const files = (value as { files?: unknown }).files;
    if (!files || typeof files !== 'object' || Array.isArray(files) || Object.keys(files).length > LIMITS.textFiles * 2) throw new Error('INVALID_REQUEST');
    const external = new Set<string>(); const unresolved: BundleView['unresolved'] = []; const candidates: BundleView['candidates'] = [];
    const missing = new Map<string, string>();
    for (const [rel, text] of Object.entries(files as Record<string, unknown>)) {
      const entry = bundle.files.get(key(rel));
      const kind = refKind(rel);
      if (!entry || entry.kind !== 'text' || !kind || typeof text !== 'string' || text.length > MAX_SCAN_TEXT) continue;
      const found = await analyze(kind, text);
      for (const host of found.external) external.add(host);
      for (const item of found.unresolved) unresolved.push({ from: entry.rel, line: item.line, spec: item.spec });
      for (const item of found.candidates) {
        const target = resolveRef(entry.rel, item.spec);
        if (target && !bundle.files.has(key(target)) && !nameReason(target)) { bundle.candidates.set(key(target), target); candidates.push({ from: entry.rel, path: target }); }
      }
      for (const item of found.local) {
        const target = resolveRef(entry.rel, item.spec);
        if (!target || bundle.files.has(key(target)) || bundle.declined.has(key(target)) || bundle.pending.has(key(target))) continue;
        const reason = await this.check(bundle, target, false);
        if (reason === null) bundle.pending.set(key(target), target);
        // A reference to a file that does not exist yet: the host may create it.
        else if (reason === 'missing' && TEXT_EXTENSIONS.has(extname(target).toLowerCase())) missing.set(key(target), target);
      }
      // Module guesses (Python import, C header sources, Java classes) count only when such a file exists.
      for (const item of found.optional) {
        const target = resolveRef(entry.rel, item.spec);
        if (!target || bundle.files.has(key(target)) || bundle.declined.has(key(target)) || bundle.pending.has(key(target))) continue;
        if (await this.check(bundle, target, false) === null) bundle.pending.set(key(target), target);
      }
    }
    return { pending: [...bundle.pending.values()], missing: [...missing.values()], external: [...external], unresolved, candidates };
  }

  async approve(value: unknown): Promise<BundleView | { declined: true }> {
    const bundle = this.bundle(value, ['bundle', 'path', 'accept']);
    const { path, accept } = value as { path?: unknown; accept?: unknown };
    if (typeof path !== 'string' || typeof accept !== 'boolean') throw new Error('INVALID_REQUEST');
    const rel = bundle.pending.get(key(path));
    if (!rel) throw new Error('NOT_PENDING');
    bundle.pending.delete(key(rel));
    if (!accept) { bundle.declined.add(key(rel)); return { declined: true }; }
    return this.include(bundle, rel);
  }

  /** Adds a new, empty text file at a root-relative path. Nothing is written until the host saves. */
  async create(value: unknown): Promise<BundleView> {
    const bundle = this.bundle(value, ['bundle', 'path']);
    const path = (value as { path?: unknown }).path;
    if (typeof path !== 'string' || path.length > 260) throw new Error('INVALID_REQUEST');
    const rel = resolveRef('', path.trim());
    if (!rel || nameReason(rel) || !TEXT_EXTENSIONS.has(extname(rel).toLowerCase()) || extname(rel).toLowerCase() === '.svg') throw new Error('NOT_ALLOWED');
    if (bundle.files.has(key(rel))) throw new Error('EXISTS');
    const abs = join(bundle.root, ...rel.split('/'));
    if (await exists(abs)) throw new Error('EXISTS');
    await this.checkParents(bundle, rel);
    const totals = this.totals(bundle);
    if (totals.textFiles >= LIMITS.textFiles) throw new Error('LIMIT');
    // New files follow the start file's line endings.
    const crlf = [...bundle.files.values()].find(entry => same(entry.rel, bundle.entry))?.crlf ?? true;
    const view: FileView = { path: rel, kind: 'text', size: 0, language: languageOf(rel), text: '' };
    bundle.files.set(key(rel), { rel, abs, kind: 'text', size: 0, hash: '', crlf, bom: false, view, created: true });
    bundle.pending.delete(key(rel)); bundle.candidates.delete(key(rel));
    return this.view(bundle, { excluded: [], unresolved: [], external: [], candidates: [] });
  }

  /** The nearest existing folder on the way to `rel` must be inside the root and reached without links or junctions. */
  private async checkParents(bundle: Bundle, rel: string): Promise<void> {
    const parts = rel.split('/').slice(0, -1);
    for (let depth = parts.length; depth >= 0; depth--) {
      const folder = join(bundle.root, ...parts.slice(0, depth));
      if (!(await exists(folder))) continue;
      let real: string;
      try { real = await nativeRealpath(folder); } catch { throw new Error('NOT_ALLOWED'); }
      const inside = relative(bundle.root, real).split(sep).join('/');
      if (!same(inside, parts.slice(0, depth).join('/')) || !(await stat(real)).isDirectory()) throw new Error('NOT_ALLOWED');
      return;
    }
    throw new Error('NOT_ALLOWED');
  }

  async save(value: unknown): Promise<{ results: { path: string; saved: boolean; error?: string }[] }> {
    const bundle = this.bundle(value, ['bundle', 'files']);
    const files = (value as { files?: unknown }).files;
    if (!Array.isArray(files) || files.length > LIMITS.textFiles * 2) throw new Error('INVALID_REQUEST');
    const results: { path: string; saved: boolean; error?: string }[] = [];
    for (const item of files as unknown[]) {
      const { path, text } = (item && typeof item === 'object' ? item : {}) as { path?: unknown; text?: unknown };
      if (typeof path !== 'string' || typeof text !== 'string' || text.length > MAX_SCAN_TEXT) throw new Error('INVALID_REQUEST');
      const entry = bundle.files.get(key(path));
      if (!entry || entry.kind !== 'text') { results.push({ path, saved: false, error: 'NOT_OPEN' }); continue; }
      let current: string | null;
      try { current = digest((await readText(entry.abs)).bytes); } catch { current = null; }
      // A new file someone else created on disk in the meantime counts as a change, like an edited one.
      const changed = entry.created ? await exists(entry.abs) : current !== entry.hash;
      if (changed && !(await this.confirmOverwrite(entry.rel))) { results.push({ path: entry.rel, saved: false, error: 'FILE_CHANGED' }); continue; }
      try {
        if (entry.created) {
          await this.checkParents(bundle, entry.rel);
          await mkdir(dirname(entry.abs), { recursive: true });
          await this.checkParents(bundle, entry.rel); // the folders just made must still be plain folders inside the root
        }
        entry.hash = await writeText({ path: entry.abs, crlf: entry.crlf, bom: entry.bom }, text);
        entry.created = false;
        results.push({ path: entry.rel, saved: true });
      } catch { results.push({ path: entry.rel, saved: false, error: 'SAVE_FAILED' }); }
    }
    return { results };
  }

  close(value: unknown): { closed: boolean } {
    const id = value && typeof value === 'object' ? (value as { bundle?: unknown }).bundle : undefined;
    return { closed: typeof id === 'string' && this.bundles.delete(id) };
  }

  closeAll(): void { this.bundles.clear(); }

  private bundle(value: unknown, keys: string[]): Bundle {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(name => !keys.includes(name))) throw new Error('INVALID_REQUEST');
    const id = (value as { bundle?: unknown }).bundle;
    const bundle = typeof id === 'string' ? this.bundles.get(id) : undefined;
    if (!bundle) throw new Error('NOT_OPEN');
    return bundle;
  }

  private totals(bundle: Bundle) {
    let textFiles = 0, textBytes = 0, binaryFiles = 0, binaryBytes = 0;
    for (const entry of bundle.files.values()) {
      if (entry.kind === 'text') { textFiles++; textBytes += entry.size; } else { binaryFiles++; binaryBytes += entry.size; }
    }
    return { textFiles, textBytes, binaryFiles, binaryBytes };
  }

  /** Why a root-relative path cannot join the set, or null. Looks at names, links and sizes; reads contents only when `read`. */
  private async check(bundle: Bundle, rel: string, read: true): Promise<Reason | Entry>;
  private async check(bundle: Bundle, rel: string, read: false): Promise<Reason | null>;
  private async check(bundle: Bundle, rel: string, read: boolean): Promise<Reason | Entry | null> {
    const named = nameReason(rel);
    if (named) return named;
    const extension = extname(rel).toLowerCase();
    const text = TEXT_EXTENSIONS.has(extension);
    const binary = BINARY[extension];
    if (!text && !binary) return 'type';
    const abs = join(bundle.root, ...rel.split('/'));
    let real: string;
    try { real = await nativeRealpath(abs); } catch { return 'missing'; }
    // A link or junction anywhere on the way resolves somewhere else (or outside): refuse it.
    const inside = relative(bundle.root, real).split(sep).join('/');
    if (!same(inside, rel)) return inside.startsWith('..') || isAbsolute(inside) ? 'outside' : 'link';
    let info;
    try { info = await lstat(real); } catch { return 'missing'; }
    if (info.isSymbolicLink()) return 'link';
    if (!info.isFile()) return 'missing';
    const totals = this.totals(bundle);
    if (text) {
      if (info.size > MAX_FILE_BYTES) return 'size';
      if (totals.textFiles >= LIMITS.textFiles || totals.textBytes + info.size > LIMITS.textBytes) return 'limit';
    } else {
      if (info.size > LIMITS.binaryFile) return 'size';
      if (totals.binaryFiles >= LIMITS.binaryFiles || totals.binaryBytes + info.size > LIMITS.binaryBytes) return 'limit';
    }
    if (!read) return null;
    const actual = inside; // the disk's own spelling
    if (text) {
      let read: Awaited<ReturnType<typeof readText>>;
      try { read = await readText(real); } catch (error) { return error instanceof Error && error.message === 'FILE_TOO_LARGE' ? 'size' : 'not-text'; }
      const view: FileView = { path: actual, kind: 'text', size: read.bytes.length, language: languageOf(actual), text: read.text.replace(/\r\n/g, '\n') };
      return { rel: actual, abs: real, kind: 'text', size: read.bytes.length, hash: digest(read.bytes), crlf: read.text.includes('\r\n'), bom: read.bom, view };
    }
    let bytes: Buffer;
    try { bytes = await readFile(real); } catch { return 'missing'; }
    if (bytes.length > LIMITS.binaryFile || (await stat(real)).size !== bytes.length) return 'size';
    if (!binary.check(bytes)) return 'signature';
    const sha256 = digest(bytes);
    const view: FileView = { path: actual, kind: 'binary', size: bytes.length, mime: binary.mime, sha256, bytes: new Uint8Array(bytes) };
    return { rel: actual, abs: real, kind: 'binary', size: bytes.length, hash: sha256, crlf: false, bom: false, mime: binary.mime, view };
  }

  private async include(bundle: Bundle, rel: string): Promise<BundleView> {
    if (!bundle.files.has(key(rel))) {
      const entry = await this.check(bundle, rel, true);
      if (typeof entry === 'string') throw new Error(entry === 'size' ? 'FILE_TOO_LARGE' : entry === 'limit' ? 'LIMIT' : entry === 'not-text' ? 'NOT_TEXT' : 'NOT_ALLOWED');
      bundle.files.set(key(entry.rel), entry);
      bundle.candidates.delete(key(entry.rel));
      await this.follow(bundle, [entry]);
    }
    return this.view(bundle, { excluded: [], unresolved: [], external: [], candidates: [] });
  }

  private async collect(bundle: Bundle): Promise<BundleView> {
    const entry = await this.check(bundle, bundle.entry, true);
    if (typeof entry === 'string') throw new Error(entry === 'size' ? 'FILE_TOO_LARGE' : entry === 'not-text' ? 'NOT_TEXT' : 'INVALID_FILE');
    bundle.entry = entry.rel;
    bundle.files.set(key(entry.rel), entry);
    const report = { excluded: [] as BundleView['excluded'], unresolved: [] as BundleView['unresolved'], external: [] as string[], candidates: [] as BundleView['candidates'] };
    await this.follow(bundle, [entry], report);
    return this.view(bundle, report);
  }

  /** Breadth-first walk over text files already in the set, adding what they load. Files on disk are the host's own. */
  private async follow(bundle: Bundle, queue: Entry[], report?: Pick<BundleView, 'excluded' | 'unresolved' | 'external' | 'candidates'>): Promise<void> {
    const excludedSeen = new Set<string>();
    for (let index = 0; index < queue.length; index++) {
      const entry = queue[index];
      const kind = refKind(entry.rel);
      if (entry.kind !== 'text' || !kind || entry.view.text === undefined) continue;
      const found: Analysis = await analyze(kind, entry.view.text);
      if (report) {
        for (const host of found.external) if (!report.external.includes(host)) report.external.push(host);
        for (const item of found.unresolved) report.unresolved.push({ from: entry.rel, line: item.line, spec: item.spec });
      }
      for (const item of found.candidates) {
        const target = resolveRef(entry.rel, item.spec);
        if (target && !bundle.files.has(key(target)) && !nameReason(target) && !bundle.candidates.has(key(target))) {
          bundle.candidates.set(key(target), target);
          report?.candidates.push({ from: entry.rel, path: target });
        }
      }
      for (const item of found.local) {
        const target = resolveRef(entry.rel, item.spec);
        const seen = `${entry.rel}\n${item.spec}`;
        if (!target) { if (report && !excludedSeen.has(seen)) { excludedSeen.add(seen); report.excluded.push({ from: entry.rel, spec: item.spec, reason: 'outside' }); } continue; }
        if (bundle.files.has(key(target))) continue;
        const next = await this.check(bundle, target, true);
        if (typeof next === 'string') {
          if (report && !excludedSeen.has(seen)) { excludedSeen.add(seen); report.excluded.push({ from: entry.rel, spec: item.spec, reason: next }); }
          continue;
        }
        bundle.files.set(key(next.rel), next);
        bundle.candidates.delete(key(next.rel));
        queue.push(next);
      }
      for (const item of found.optional) {
        const target = resolveRef(entry.rel, item.spec);
        if (!target || bundle.files.has(key(target)) || nameReason(target)) continue;
        const next = await this.check(bundle, target, true);
        if (typeof next === 'string') continue; // not a file of this set (standard library, other package …)
        bundle.files.set(key(next.rel), next);
        bundle.candidates.delete(key(next.rel));
        queue.push(next);
      }
    }
  }

  private async view(bundle: Bundle, report: Pick<BundleView, 'excluded' | 'unresolved' | 'external' | 'candidates'>): Promise<BundleView> {
    const files = [...bundle.files.values()].map(entry => entry.view);
    const canWiden = (await widerRoots(bundle.root, this.home)).length > 0;
    return { bundle: bundle.id, entry: bundle.entry, root: `…${sep}${basename(bundle.root)}`, canWiden, files, ...report,
      candidates: [...bundle.candidates.values()].map(path => ({ from: report.candidates.find(item => item.path === path)?.from ?? '', path })) };
  }
}

