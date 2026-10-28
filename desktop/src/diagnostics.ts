import type { Health } from '../contracts/workspace';

/** A fixed metadata projection; never serialize arbitrary errors, process.env or agent payloads. */
export function diagnostics(health: Health, version: string, now = new Date()): string {
  if (!/^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$/.test(version)) throw new Error('INVALID_REQUEST');
  const state = (value: unknown) => ['ready', 'degraded', 'unavailable'].includes(String(value)) ? value : 'unavailable';
  const errorCode = ['RECOVERY_REQUIRED', 'UNSUPPORTED_PROTOCOL', 'STORAGE_UNAVAILABLE', 'ACCESS_DENIED'].includes(health.errorCode ?? '') ? health.errorCode : undefined;
  return JSON.stringify({ format: 'gitviz-diagnostics-v1', generatedAt: now.toISOString(), appVersion: version,
    protocolVersion: 1, supportedLocalSchema: 8, platform: 'win32', architecture: 'x64',
    health: { state: state(health.state), components: { store: state(health.components.store), secrets: state(health.components.secrets), lifecycle: state(health.components.lifecycle) }, ...(errorCode ? { errorCode } : {}) },
    contents: 'Versions and component states only; no account, token, URL, path, source, database or raw logs.' }, null, 2) + '\n';
}
