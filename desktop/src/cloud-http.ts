import { request } from 'node:https';

export const CLOUD = {
  'claude-opus-5-5-v1': { provider: 'anthropic', model: 'claude-opus-5-5', host: 'api.anthropic.com', path: '/v1/messages' },
  'claude-sonnet-5-5-v1': { provider: 'anthropic', model: 'claude-sonnet-5-5', host: 'api.anthropic.com', path: '/v1/messages' },
  'gpt-5.4-v1': { provider: 'openai', model: 'gpt-5.4-2026-03-05', host: 'api.openai.com', path: '/v1/responses' },
  'gemini-3.5-flash-lite-v1': { provider: 'gemini', model: 'gemini-3.5-flash-lite', host: 'generativelanguage.googleapis.com', path: '/v1beta/models/gemini-3.5-flash-lite:generateContent' },
} as const;
export interface CloudReply { httpStatus: number; rawBase64: string; errorCode: 'PROVIDER_ERROR' | 'PROVIDER_TIMEOUT' | 'OUTPUT_LIMIT' | null; uncertain: boolean }
export type CloudHttp = (profileId: keyof typeof CLOUD, wire: Buffer, apiKey: string, signal: AbortSignal) => Promise<CloudReply>;
const debugAtStartup = /http|\*/i.test(process.env.NODE_DEBUG ?? '');

/** The only cloud network boundary. Key in request headers only; no env proxy, redirect or retry path. */
export function createCloudHttp(transport: typeof request = request): CloudHttp { return (profileId, wire, apiKey, signal) => new Promise((resolve, reject) => {
  const route = CLOUD[profileId];
  if (debugAtStartup || /http|\*/i.test(process.env.NODE_DEBUG ?? '') || !Object.hasOwn(CLOUD, profileId) || !route || wire.length > 163840 || !/^[!-~]{16,512}$/.test(apiKey)) { reject(new Error('INVALID_REQUEST')); return; }
  const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'application/json', 'Content-Length': String(wire.length) };
  if (route.provider === 'openai') headers.Authorization = `Bearer ${apiKey}`;
  else if (route.provider === 'anthropic') { headers['x-api-key'] = apiKey; headers['anthropic-version'] = '2023-06-01'; }
  else headers['x-goog-api-key'] = apiKey;
  const child = transport({ hostname: route.host, port: 443, path: route.path, method: 'POST', headers, signal,
    agent: false, rejectUnauthorized: true, timeout: 90000 }, response => {
    const chunks: Buffer[] = []; let size = 0;
    response.on('data', (chunk: Buffer) => { size += chunk.length; if (size > 163840) child.destroy(new Error('OUTPUT_LIMIT')); else chunks.push(chunk); });
    response.once('error', () => reject(new Error('PROVIDER_ERROR')));
    response.once('end', () => resolve({ httpStatus: response.statusCode ?? 0, rawBase64: Buffer.concat(chunks).toString('base64'), errorCode: null, uncertain: false }));
  });
  child.once('timeout', () => child.destroy(new Error('PROVIDER_TIMEOUT')));
  child.once('error', error => reject(new Error(['PROVIDER_TIMEOUT', 'OUTPUT_LIMIT'].includes(error.message) ? error.message : 'PROVIDER_ERROR')));
  child.end(wire);
}); }
export const cloudHttp: CloudHttp = createCloudHttp();
