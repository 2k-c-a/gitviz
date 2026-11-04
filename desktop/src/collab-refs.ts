// Reference analysis for co-editing file sets (AD-34). Pure functions over text: no file system access.
// Each file yields the specifiers it loads (local, external, unresolved) and "candidates" the host may add by hand
// (page links and fetch() string literals), with 1-based line numbers for display.

export type RefKind = 'html' | 'css' | 'js' | 'svg';
export interface Ref { spec: string; line: number }
export interface Analysis { local: Ref[]; external: string[]; unresolved: Ref[]; candidates: Ref[] }

const EXTENSION_KINDS: Record<string, RefKind> = { '.html': 'html', '.htm': 'html', '.css': 'css', '.js': 'js', '.mjs': 'js', '.cjs': 'js', '.svg': 'svg' };
export function refKind(path: string): RefKind | null {
  const dot = path.lastIndexOf('.');
  return dot < 0 ? null : EXTENSION_KINDS[path.slice(dot).toLowerCase()] ?? null;
}

type SpecClass = { type: 'local'; path: string } | { type: 'external'; host: string } | { type: 'ignore' } | { type: 'unresolved' };

/** Classifies one URL-ish specifier. Bare names (`lodash`) are unresolved only for JS imports. */
export function classify(raw: string, bareIsPackage = false): SpecClass {
  const spec = raw.trim();
  if (!spec || spec.startsWith('#') || spec.length > 2000) return { type: 'ignore' };
  if (spec.startsWith('//')) { try { return { type: 'external', host: new URL(`https:${spec}`).host }; } catch { return { type: 'unresolved' }; } }
  const scheme = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(spec);
  if (scheme && !/^[a-zA-Z]$/.test(scheme[1])) {
    const name = scheme[1].toLowerCase();
    if (name === 'http' || name === 'https' || name === 'ws' || name === 'wss') { try { return { type: 'external', host: new URL(spec).host }; } catch { return { type: 'unresolved' }; } }
    return { type: 'ignore' }; // data:, blob:, javascript:, mailto: and other schemes load nothing from the file set
  }
  const path = spec.replace(/[?#].*$/s, '');
  if (!path) return { type: 'ignore' };
  if (bareIsPackage && !/^(?:\.{1,2}\/|\/)/.test(path)) return { type: 'unresolved' };
  return { type: 'local', path };
}

const lineAt = (text: string, index: number) => { let line = 1; for (let i = 0; i < index && i < text.length; i++) if (text.charCodeAt(i) === 10) line++; return line; };

class Collector {
  readonly result: Analysis = { local: [], external: [], unresolved: [], candidates: [] };
  private readonly hosts = new Set<string>();
  add(spec: string, line: number, bareIsPackage = false): void {
    const value = classify(spec, bareIsPackage);
    if (value.type === 'local') this.result.local.push({ spec: value.path, line });
    else if (value.type === 'external') { if (!this.hosts.has(value.host)) { this.hosts.add(value.host); this.result.external.push(value.host); } }
    else if (value.type === 'unresolved') this.result.unresolved.push({ spec: spec.trim().slice(0, 200), line });
  }
  candidate(spec: string, line: number): void {
    const value = classify(spec);
    if (value.type === 'local') this.result.candidates.push({ spec: value.path, line });
    else if (value.type === 'external' && !this.hosts.has(value.host)) { this.hosts.add(value.host); this.result.external.push(value.host); }
  }
  unresolved(text: string, line: number): void { this.result.unresolved.push({ spec: text.replace(/\s+/g, ' ').trim().slice(0, 200), line }); }
}

// ── CSS ────────────────────────────────────────────────────────────────────────
function css(text: string, out: Collector, offset = 0): void {
  // Blank out comments but keep offsets so line numbers stay right.
  const source = text.replace(/\/\*[\s\S]*?\*\//g, match => match.replace(/[^\n]/g, ' '));
  const seen = new Set<number>();
  for (const match of source.matchAll(/@import\s+(?:url\(\s*)?(["']?)([^"')\s;]+)\1/gi)) {
    seen.add(match.index + match[0].indexOf(match[2]));
    out.add(match[2], offset + lineAt(text, match.index));
  }
  for (const match of source.matchAll(/url\(\s*(["']?)([^"')]*?)\1\s*\)/gi)) {
    if (seen.has(match.index + match[0].indexOf(match[2]))) continue;
    out.add(match[2], offset + lineAt(text, match.index));
  }
}

// ── JavaScript ─────────────────────────────────────────────────────────────────
// es-module-lexer is an ES module (a small WASM lexer that skips comments and strings); load it once, lazily.
let lexer: Promise<typeof import('es-module-lexer', { with: { 'resolution-mode': 'import' } })> | null = null;
async function js(text: string, out: Collector, offset = 0): Promise<void> {
  lexer ??= import('es-module-lexer').then(async module => { await module.init(); return module; });
  const { parse } = await lexer;
  let parsed = false;
  try {
    const [imports] = parse(text);
    for (const item of imports) {
      if (item.type === 'import-meta' || (item.type !== 'dynamic' && item.typeOnly)) continue;
      const line = offset + lineAt(text, item.importStart);
      if (typeof item.specifier === 'string') out.add(item.specifier, line, true);
      else out.unresolved(text.slice(item.importStart, item.importEnd), line); // import(variable)
    }
    parsed = true;
  } catch { /* TypeScript, JSX or a syntax error: fall back to plain patterns below */ }
  if (!parsed) {
    for (const match of text.matchAll(/(?:^|[;\s])(?:import|export)\s[^'"`;]*?from\s*(["'])([^"'\n]+)\1|(?:^|[;\s])import\s*(["'])([^"'\n]+)\3|\bimport\(\s*(["'])([^"'\n]+)\5\s*\)/gm)) {
      out.add(match[2] ?? match[4] ?? match[6], offset + lineAt(text, match.index), true);
    }
  }
  for (const match of text.matchAll(/\bnew\s+(?:Shared)?Worker\(\s*(["'`])([^"'`\n$]+)\1|\bnew\s+URL\(\s*(["'`])([^"'`\n$]+)\3\s*,\s*import\.meta\.url\s*\)/g)) {
    out.add(match[2] ?? match[4], offset + lineAt(text, match.index));
  }
  for (const match of text.matchAll(/\bfetch\(\s*(?:(["'`])([^"'`\n$]+)\1|([^)\n]{1,80}))/g)) {
    const line = offset + lineAt(text, match.index);
    if (match[2] !== undefined) out.candidate(match[2], line); else out.unresolved(`fetch(${match[3]})`, line);
  }
}

// ── HTML ───────────────────────────────────────────────────────────────────────
interface Node { nodeName: string; tagName?: string; attrs?: { name: string; value: string }[]; childNodes?: Node[]; content?: Node; value?: string;
  sourceCodeLocation?: { startLine: number } | null }
const LINK_RELS = new Set(['stylesheet', 'icon', 'shortcut', 'apple-touch-icon', 'preload', 'modulepreload', 'manifest', 'prefetch']);
const srcset = (value: string) => value.split(',').map(part => part.trim().split(/\s+/)[0]).filter(Boolean);

async function html(text: string, out: Collector): Promise<void> {
  const { parse } = await import('parse5');
  const document = parse(text, { sourceCodeLocationInfo: true }) as unknown as Node;
  const pending: Promise<void>[] = [];
  const visit = (node: Node) => {
    const line = node.sourceCodeLocation?.startLine ?? 1;
    const attr = (name: string) => node.attrs?.find(item => item.name === name)?.value;
    const tag = node.tagName;
    if (tag) {
      const style = attr('style');
      if (style) css(style, out, line - 1);
      if (tag === 'link') {
        const rels = (attr('rel') ?? '').toLowerCase().split(/\s+/);
        const href = attr('href');
        if (href !== undefined && rels.some(rel => LINK_RELS.has(rel))) out.add(href, line);
      } else if (tag === 'script') {
        const src = attr('src');
        if (src !== undefined) out.add(src, line);
        else {
          const type = (attr('type') ?? '').toLowerCase();
          const body = (node.childNodes ?? []).map(child => child.value ?? '').join('');
          if (body && (!type || type === 'module' || type.includes('javascript'))) {
            const start = node.childNodes?.[0]?.sourceCodeLocation?.startLine ?? line;
            pending.push(js(body, out, start - 1));
          }
        }
      } else if (tag === 'style') {
        const body = (node.childNodes ?? []).map(child => child.value ?? '').join('');
        css(body, out, (node.childNodes?.[0]?.sourceCodeLocation?.startLine ?? line) - 1);
      } else if (tag === 'img' || tag === 'source' || tag === 'iframe' || tag === 'embed' || tag === 'track' || tag === 'audio' || tag === 'video' || tag === 'input') {
        const src = attr('src'); if (src !== undefined && (tag !== 'input' || (attr('type') ?? '').toLowerCase() === 'image')) out.add(src, line);
        const set = attr('srcset'); if (set) for (const item of srcset(set)) out.add(item, line);
        const poster = attr('poster'); if (poster) out.add(poster, line);
      } else if (tag === 'object') {
        const data = attr('data'); if (data) out.add(data, line);
      } else if (tag === 'use' || tag === 'image' || tag === 'feImage') {
        const href = attr('href') ?? attr('xlink:href'); if (href) out.add(href, line);
      } else if (tag === 'a' || tag === 'area') {
        const href = attr('href');
        if (href && /\.html?(?:[?#]|$)/i.test(href)) out.candidate(href, line);
      }
    }
    for (const child of node.childNodes ?? []) visit(child);
    if (node.content) visit(node.content); // <template>
  };
  visit(document);
  await Promise.all(pending);
}

// ── SVG (a standalone file) ────────────────────────────────────────────────────
function svg(text: string, out: Collector): void {
  for (const match of text.matchAll(/<(?:use|image|feImage|script)\b[^>]*?\s(?:xlink:)?(?:href|src)\s*=\s*(["'])(.*?)\1/gis)) out.add(match[2], lineAt(text, match.index));
  for (const match of text.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)) css(match[1], out, lineAt(text, match.index) - 1);
  for (const match of text.matchAll(/\sstyle\s*=\s*(["'])(.*?)\1/gis)) css(match[2], out, lineAt(text, match.index) - 1);
}

/** Analyzes one file's text. Errors in one file never stop the rest of the set. */
export async function analyze(kind: RefKind, text: string): Promise<Analysis> {
  const out = new Collector();
  try {
    if (kind === 'html') await html(text, out);
    else if (kind === 'css') css(text, out);
    else if (kind === 'js') await js(text, out);
    else svg(text, out);
  } catch { /* An unparsable file contributes what was collected so far. */ }
  return out.result;
}
