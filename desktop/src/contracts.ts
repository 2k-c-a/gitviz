import Ajv from 'ajv';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { TextDecoder } from 'node:util';
import type { Request, Response } from '../contracts/workspace';

export const MAX_FRAME = 262144;
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
export class ProtocolError extends Error {
  constructor() { super('INVALID_REQUEST'); }
}

/** Recursive JSON recognizer catches duplicate keys before JSON.parse loses them. */
export function decode(raw: Uint8Array): unknown {
  if (!raw.length || raw.length > MAX_FRAME) throw new ProtocolError();
  let text: string;
  try { text = decoder.decode(raw); } catch { throw new ProtocolError(); }
  let at = 0;
  const whitespace = () => { while (/[\x20\t\r\n]/.test(text[at] ?? 'x')) at++; };
  function string(): string {
    const start = at++;
    while (at < text.length) {
      if (text[at] === '\\') { at += 2; continue; }
      if (text[at++] === '"') {
        const value: unknown = JSON.parse(text.slice(start, at));
        if (typeof value !== 'string' || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) throw new ProtocolError();
        return value;
      }
    }
    throw new ProtocolError();
  }
  function value(depth: number): void {
    whitespace();
    const c = text[at];
    if (c === '{' || c === '[') {
      if (++depth > 16) throw new ProtocolError();
      const object = c === '{'; const end = object ? '}' : ']';
      const keys = new Set<string>(); at++; whitespace();
      if (text[at] === end) { at++; return; }
      while (at < text.length) {
        if (object) {
          if (text[at] !== '"') throw new ProtocolError();
          const key = string();
          if (keys.has(key)) throw new ProtocolError();
          keys.add(key); whitespace();
          if (text[at++] !== ':') throw new ProtocolError();
        }
        value(depth); whitespace();
        if (text[at] === end) { at++; return; }
        if (text[at++] !== ',') throw new ProtocolError();
        whitespace();
      }
      throw new ProtocolError();
    }
    if (c === '"') { string(); return; }
    const token = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(at))?.[0];
    if (!token) throw new ProtocolError();
    if (!['true', 'false', 'null'].includes(token) && !Number.isFinite(Number(token))) throw new ProtocolError();
    at += token.length;
  }
  try {
    value(0); whitespace();
    if (at !== text.length) throw new ProtocolError();
    return JSON.parse(text) as unknown;
  } catch { throw new ProtocolError(); }
}

export class Contracts {
  private readonly validators;
  private readonly results;
  constructor(directory: string) {
    const ajv = new Ajv({ strict: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
    this.validators = Object.fromEntries(['request', 'response', 'bootstrap'].map(name =>
      [name, ajv.compile(JSON.parse(readFileSync(join(directory, `${name}.schema.json`), 'utf8')) as object)]));
    const response = JSON.parse(readFileSync(join(directory, 'response.schema.json'), 'utf8')) as { definitions: Record<string, object> };
    this.results = Object.fromEntries(Object.keys(response.definitions).map(name => [name, ajv.compile({ definitions: response.definitions, $ref: `#/definitions/${name}` })]));
  }
  validate(kind: 'request' | 'response' | 'bootstrap', value: unknown): void {
    if (!this.validators[kind]?.(value)) throw new ProtocolError();
  }
  request(value: unknown): asserts value is Request { this.validate('request', value); }
  response(value: unknown): asserts value is Response { this.validate('response', value); }
  result(operation: string, value: unknown): void {
    const extra: Record<string, string> = { 'workspace.impact.historical': 'historicalResult', 'workspace.remote.connections': 'remoteConnectionsList', 'workspace.reviews.cloudExchange': 'cloudExchange', 'workspace.reviews.cloudContext': 'cloudContextAccepted', 'workspace.projects.connectRemote': 'remoteConnectionResult', 'workspace.remote.observeAnalysis': 'remoteObserved', 'workspace.remote.getAnalysis': 'remoteAnalysis', 'workspace.notifications.list': 'notificationList', 'workspace.notifications.markRead': 'notificationItem', 'workspace.events.read': 'eventBatch', 'workspace.projects.setEditor': 'project',  'workspace.impact.release': 'releasedImpact',  'workspace.files.locate': 'fileLocation', 'workspace.validation.prepare': 'validationPrepared', 'workspace.validation.approve': 'validationApproval', 'workspace.validation.start': 'validationRun', 'workspace.validation.get': 'validationRun', 'workspace.validation.cancel': 'validationRun', 'workspace.comparisons.create': 'comparison', 'workspace.comparisons.get': 'comparison',  'workspace.proposals.listRecovery': 'recoveryList', 'workspace.proposals.create': 'proposal', 'workspace.proposals.get': 'proposal', 'workspace.proposals.reject': 'proposal', 'workspace.proposals.requestRevision': 'reviewPreview', 'workspace.proposals.approve': 'proposalApproval', 'workspace.proposals.apply': 'proposal', 'workspace.proposals.recoveryPlan': 'recoveryPlan', 'workspace.proposals.approveRecovery': 'proposalApproval', 'workspace.proposals.recover': 'proposal', 'workspace.impact.prepare': 'impactPrepared', 'workspace.impact.read': 'impactChunk', 'workspace.impact.append': 'impactAccepted', 'workspace.impact.finish': 'impactResult', 'workspace.impact.get': 'impactResult', 'workspace.reviews.preview': 'reviewPreview', 'workspace.reviews.start': 'reviewRun', 'workspace.reviews.get': 'reviewRun', 'workspace.reviews.list': 'reviewList', 'workspace.reviews.cancel': 'reviewRun', 'workspace.reviews.feedback': 'reviewRun', 'workspace.changes.capture': 'snapshot', 'workspace.files.preview': 'preview', 'workspace.validation.detect': 'detection', 'workspace.validation.plan': 'plan' };
    const name = extra[operation] ?? (operation.endsWith('selectAndRegister') ? ((value as { selectionId?: unknown }).selectionId !== undefined ? 'selection' : 'project') :
      operation.endsWith('.list') ? 'projects' : operation.endsWith('.status') ? 'git' : operation.endsWith('.unregister') ? 'unregistered' : 'project');
    if (operation.startsWith('workspace.system.')) return;
    if (!this.results[name]?.(value)) throw new ProtocolError();
  }
}

export class FrameReader {
  private buffered = Buffer.alloc(0);
  push(chunk: Buffer): unknown[] {
    const frames: unknown[] = [];
    let offset = 0;
    while (offset < chunk.length) {
      const newline = chunk.indexOf(10, offset);
      const end = newline < 0 ? chunk.length : newline;
      if (this.buffered.length + end - offset > MAX_FRAME) throw new ProtocolError();
      this.buffered = Buffer.concat([this.buffered, chunk.subarray(offset, end)]);
      if (newline < 0) break;
      frames.push(decode(this.buffered)); this.buffered = Buffer.alloc(0); offset = end + 1;
    }
    return frames;
  }
  finish(): void { if (this.buffered.length) throw new ProtocolError(); }
}
