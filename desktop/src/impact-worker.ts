/** Fixed compiler, virtual snapshot host, no project config/plugins/emit or filesystem resolver. */
import * as ts from 'typescript';
import { createHash } from 'node:crypto';
import { measureTypescript } from './metrics';

export interface Source { fileId: string; path: string; facet: string; sourceHash: string; text: string }
export interface Reference {
  kind: 'static_reference'; observed: true; fileId: string; path: string; facet: string; sourceHash: string;
  referenceKind: 'import' | 'call' | 'type'; symbol: string; startByte: number; endByte: number; line: number;
  column: number; resolved: boolean; targetFileId: string | null; targetLine: number | null; toolVersion: 'typescript-6.0.3-v1';
}
function virtual(file: Source): string { return `/${file.facet}/${file.path}`; }
function relativeModule(containing: string, specifier: string): string | null {
  if (!specifier.startsWith('.') || /[\\\0:]/.test(specifier)) return null;
  const parts = containing.split('/').filter(Boolean).slice(0, -1);
  for (const part of specifier.split('/')) {
    if (part === '.' || part === '') continue;
    if (part === '..') { if (parts.length <= 1) return null; parts.pop(); } else parts.push(part);
  }
  return '/' + parts.join('/');
}
export function parseTypescript(files: Source[]): { records: Reference[]; limitations: string[] } {
  if (ts.version !== '6.0.3' || files.length > 50 || files.reduce((n, file) => n + Buffer.byteLength(file.text), 0) > 2000000) throw new Error('INPUT_TOO_LARGE');
  for (const file of files) if (Buffer.byteLength(file.text) > 200000 || /[\\\0:]/.test(file.path) || file.path.split('/').some(p => !p || p === '.' || p === '..') || createHash('sha256').update(file.text).digest('hex') !== file.sourceHash) throw new Error('INVALID_REQUEST');
  const byName = new Map(files.map(file => [virtual(file), file]));
  const sources = new Map(files.map(file => [virtual(file), ts.createSourceFile(virtual(file), file.text, ts.ScriptTarget.Latest, true)]));
  const extensions = ['.ts', '.tsx', '.js', '.jsx', '.mts', '.cts', '.mjs', '.cjs'];
  const resolve = (name: string, containing: string) => {
    const base = relativeModule(containing, name); if (!base) return undefined;
    return [base, ...extensions.map(ext => base + ext), ...extensions.map(ext => base + '/index' + ext)].find(path => byName.has(path));
  };
  const host: ts.CompilerHost = {
    getSourceFile: name => sources.get(name), getDefaultLibFileName: () => '/no-lib.d.ts', writeFile: () => { throw new Error('ACCESS_DENIED'); },
    getCurrentDirectory: () => '/', getDirectories: () => [], fileExists: name => byName.has(name), readFile: name => byName.get(name)?.text,
    getCanonicalFileName: name => name, useCaseSensitiveFileNames: () => true, getNewLine: () => '\n',
    resolveModuleNames: (names, containing) => names.map(name => { const found = resolve(name, containing); return found ? { resolvedFileName: found } : undefined; }),
  };
  const program = ts.createProgram([...sources.keys()], { noLib: true, noEmit: true, allowJs: true, checkJs: true, target: ts.ScriptTarget.Latest, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve }, host);
  const checker = program.getTypeChecker();
  const records: Reference[] = [];
  const limitations = new Set(['syntax-only-not-runtime-callgraph', 'external-modules-and-dynamic-dispatch-unresolved']);
  let nodes = 0; let bytes = 0;
  const reassigned = new Set<string>(); let dynamic = false;
  for (const source of sources.values()) {
    const collectWrites = (node: ts.Node): void => { if (ts.isIdentifier(node)) reassigned.add(node.text); ts.forEachChild(node, collectWrites); };
    const scan = (node: ts.Node): void => {
      if (++nodes > 60000) throw new Error('INPUT_TOO_LARGE');
      if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment) collectWrites(node.left);
      if ((ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node)) && [ts.SyntaxKind.PlusPlusToken, ts.SyntaxKind.MinusMinusToken].includes(node.operator)) collectWrites(node.operand);
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'eval') dynamic = true;
      if (ts.isWithStatement(node)) dynamic = true;
      ts.forEachChild(node, scan);
    };
    scan(source);
  }
  for (const [name, source] of sources) {
    if (program.getSyntacticDiagnostics(source).length) { limitations.add('typescript-parse-failed'); continue; }
    const file = byName.get(name)!;
    function visit(node: ts.Node): void {
      let token: ts.Node | undefined; let kind: Reference['referenceKind'] = 'call'; let declaration: ts.Declaration | undefined;
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) { token = node.moduleSpecifier; kind = 'import'; }
      else if (ts.isCallExpression(node)) {
        token = node.expression;
        if (ts.isIdentifier(token) && !dynamic && !reassigned.has(token.text)) {
          let symbol = checker.getSymbolAtLocation(token);
          if (symbol && symbol.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
          const declarations = symbol?.declarations;
          if (declarations?.length === 1 && (ts.isFunctionDeclaration(declarations[0]!) || (ts.isVariableDeclaration(declarations[0]!) && declarations[0]!.initializer && (ts.isArrowFunction(declarations[0]!.initializer) || ts.isFunctionExpression(declarations[0]!.initializer))))) declaration = declarations[0];
        }
      } else if (ts.isTypeReferenceNode(node)) { token = node.typeName; kind = 'type'; }
      if (token) {
        const start = token.getStart(source); const end = token.getEnd(); const symbol = source.text.slice(start, end);
        const prefix = source.text.slice(0, start); const line = prefix.split('\n').length;
        const lineStart = prefix.lastIndexOf('\n') + 1;
        const columnPrefix = source.text.slice(lineStart, start);
        const columnText = line === 1 && columnPrefix.startsWith('\ufeff') ? columnPrefix.slice(1) : columnPrefix;
        const targetFile = declaration ? byName.get(declaration.getSourceFile().fileName) : undefined;
        const record: Reference = { kind: 'static_reference', observed: true, fileId: file.fileId, path: file.path, facet: file.facet, sourceHash: file.sourceHash,
          referenceKind: kind, symbol, startByte: Buffer.byteLength(source.text.slice(0, start)), endByte: Buffer.byteLength(source.text.slice(0, end)),
          line, column: [...columnText].length + 1, resolved: Boolean(targetFile),
          targetFileId: targetFile?.fileId ?? null, targetLine: targetFile && declaration ? declaration.getSourceFile().text.slice(0, declaration.getStart()).split('\n').length : null, toolVersion: 'typescript-6.0.3-v1' };
        const size = Buffer.byteLength(JSON.stringify(record));
        if (symbol.length <= 256 && records.length < 500 && bytes + size < 180000) { records.push(record); bytes += size; } else limitations.add('typescript-record-limit');
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  return { records, limitations: [...limitations] };
}

process.parentPort?.on('message', event => {
  try {
    const input = event.data as { files: Source[]; mode?: string };
    process.parentPort?.postMessage(input.mode === 'metrics' ? { ok: true, metrics: measureTypescript(input.files) } : { ok: true, ...parseTypescript(input.files) });
  } catch { process.parentPort?.postMessage({ ok: false, code: 'PARSER_UNAVAILABLE' }); }
});
