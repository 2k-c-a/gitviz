/** Fixed snapshot metrics; no project configuration, plugins, resolution or emit. */
import * as ts from 'typescript';
import { createHash } from 'node:crypto';
import type { Source } from './impact-worker';

export interface TypescriptMetric {
  fileId: string; path: string; facet: string; sourceHash: string; toolVersion: 'typescript-6.0.3-metrics-v1';
  functionCount: number | null; branchMetric: number | null; tokenWindows: number | null; duplicateWindows: number | null; limitations: string[];
}
export function measureTypescript(files: Source[]): TypescriptMetric[] {
  if (ts.version !== '6.0.3' || files.length > 50 || files.reduce((sum, file) => sum + Buffer.byteLength(file.text), 0) > 2000000) throw new Error('INPUT_TOO_LARGE');
  const windows: string[][] = [];
  const counts = new Map<string, number>();
  let budget = 60000;
  const records = files.map(file => {
    if (Buffer.byteLength(file.text) > 200000 || createHash('sha256').update(file.text).digest('hex') !== file.sourceHash) throw new Error('INVALID_REQUEST');
    const record: TypescriptMetric = { fileId: file.fileId, path: file.path, facet: file.facet, sourceHash: file.sourceHash,
      toolVersion: 'typescript-6.0.3-metrics-v1', functionCount: null, branchMetric: null, tokenWindows: null, duplicateWindows: null, limitations: [] };
    const source = ts.createSourceFile(file.path, file.text, ts.ScriptTarget.Latest, true);
    const diagnostics = (source as ts.SourceFile & { parseDiagnostics: readonly ts.Diagnostic[] }).parseDiagnostics;
    let nodes = 0, functions = 0, branches = 0;
    function visit(node: ts.Node): void {
      if (++nodes > 60000) throw new Error('NODE_LIMIT');
      if (ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node) || ts.isMethodDeclaration(node) ||
          ts.isConstructorDeclaration(node) || ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node)) functions++;
      if (ts.isIfStatement(node) || ts.isConditionalExpression(node) || ts.isForStatement(node) || ts.isForOfStatement(node) || ts.isForInStatement(node) ||
          ts.isWhileStatement(node) || ts.isDoStatement(node) || ts.isCaseClause(node) || ts.isCatchClause(node)) branches++;
      if (ts.isBinaryExpression(node) && [ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(node.operatorToken.kind)) branches++;
      ts.forEachChild(node, visit);
    }
    const hashes: string[] = [];
    try {
      if (diagnostics.length) throw new Error('SYNTAX_UNAVAILABLE');
      visit(source);
      record.functionCount = functions; record.branchMetric = functions + branches;
      const scanner = ts.createScanner(ts.ScriptTarget.Latest, true, /\.[jt]sx$/i.test(file.path) ? ts.LanguageVariant.JSX : ts.LanguageVariant.Standard, file.text);
      const tokens: string[] = [];
      for (let kind = scanner.scan(); kind !== ts.SyntaxKind.EndOfFileToken; kind = scanner.scan()) {
        if (tokens.length >= 60000) throw new Error('TOKEN_LIMIT');
        tokens.push(`${kind}:${scanner.getTokenText()}`);
      }
      const count = Math.max(0, tokens.length - 19);
      if (count > budget) throw new Error('TOKEN_LIMIT');
      budget -= count;
      for (let i = 0; i + 20 <= tokens.length; i++) {
        const hash = createHash('sha256').update(JSON.stringify(tokens.slice(i, i + 20))).digest('hex');
        hashes.push(hash); counts.set(hash, (counts.get(hash) ?? 0) + 1);
      }
      record.tokenWindows = hashes.length;
    } catch {
      record.limitations.push('syntax-node-or-token-bound-unavailable');
    }
    windows.push(hashes);
    return record;
  });
  for (let index = 0; index < records.length; index++) {
    if (records[index]!.tokenWindows !== null) records[index]!.duplicateWindows = windows[index]!.filter(hash => counts.get(hash)! > 1).length;
  }
  return records;
}
