import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';

/** Manual update verification only: no download, install or restart path exists here. */
export interface UpdateManifest { version: string; assetName: string; sha256: string; size: number; url: string }
export interface UpdatePolicy {
  /** `https://github.com/<owner>/<repo>/releases/download/` prefix. Empty until the release location is configured. */
  releasePrefix: string;
  /** Expected Authenticode subject. Empty until a signing certificate exists; installers then stay unverified. */
  publisher: string;
}
export type SignatureVerifier = (filePath: string, publisher: string) => Promise<boolean>;
export type UpdateCheck =
  | { status: 'unconfigured' | 'invalid' | 'current' }
  | { status: 'available'; manifest: UpdateManifest };
export type InstallerCheck = 'verified' | 'unsigned-not-trusted' | 'size-mismatch' | 'hash-mismatch' | 'signature-mismatch';

const VERSION = /^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/;
const ASSET = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.exe$/;
const MAX_INSTALLER_BYTES = 1024 * 1024 * 1024;

export function compareVersions(left: string, right: string): number {
  const a = left.split('.').map(Number), b = right.split('.').map(Number);
  for (let index = 0; index < 3; index++) if (a[index] !== b[index]) return a[index]! < b[index]! ? -1 : 1;
  return 0;
}

/** Strict allowlist parse: unknown keys, non-release URLs and asset/URL mismatches are rejected. */
export function parseManifest(value: unknown, policy: UpdatePolicy): UpdateManifest | null {
  if (!policy.releasePrefix.startsWith('https://') || !policy.releasePrefix.endsWith('/') || typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (Object.keys(record).sort().join() !== 'assetName,sha256,size,url,version') return null;
  const { version, assetName, sha256, size, url } = record;
  if (typeof version !== 'string' || !VERSION.test(version) || typeof assetName !== 'string' || !ASSET.test(assetName)) return null;
  if (typeof sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(sha256)) return null;
  if (typeof size !== 'number' || !Number.isSafeInteger(size) || size < 1 || size > MAX_INSTALLER_BYTES) return null;
  if (typeof url !== 'string' || url !== `${policy.releasePrefix}v${version}/${assetName}`) return null;
  return { version, assetName, sha256, size, url };
}

export function checkUpdate(currentVersion: string, manifestJson: unknown, policy: UpdatePolicy): UpdateCheck {
  if (!policy.releasePrefix) return { status: 'unconfigured' };
  const manifest = parseManifest(manifestJson, policy);
  if (!manifest || !VERSION.test(currentVersion)) return { status: 'invalid' };
  return compareVersions(manifest.version, currentVersion) > 0 ? { status: 'available', manifest } : { status: 'current' };
}

async function sha256Of(filePath: string): Promise<string> {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(filePath)) hash.update(chunk as Buffer);
  return hash.digest('hex');
}

/** Size, SHA-256, then publisher signature. Without a configured publisher an installer is never trusted. */
export async function verifyInstaller(filePath: string, manifest: UpdateManifest, policy: UpdatePolicy, verifySignature: SignatureVerifier): Promise<InstallerCheck> {
  if ((await stat(filePath)).size !== manifest.size) return 'size-mismatch';
  if (await sha256Of(filePath) !== manifest.sha256) return 'hash-mismatch';
  if (!policy.publisher) return 'unsigned-not-trusted';
  return await verifySignature(filePath, policy.publisher) ? 'verified' : 'signature-mismatch';
}
