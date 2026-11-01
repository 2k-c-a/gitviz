import { X509Certificate } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { isIP } from 'node:net';

/** A bare HTTPS origin (no path, query or credentials), or null. */
export function validOrigin(value: unknown): string | null {
  if (typeof value !== 'string' || !value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.origin === value && !url.username && !url.password ? value : null;
  } catch { return null; }
}

/** Origin a release build embedded (dist/config/server.json); not a secret. Missing or invalid means none. */
export function readEmbeddedOrigin(file: string): string | null {
  try { return validOrigin((JSON.parse(readFileSync(file, 'utf8')) as { origin?: unknown }).origin); } catch { return null; }
}

const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]']);

/**
 * Self-signed certificate a local staging build embedded next to its origin, so the app trusts that server without
 * NODE_EXTRA_CA_CERTS. Accepted only for a loopback origin and only when the certificate itself names that host;
 * release builds (a real server origin) can never carry one.
 */
export function embeddedCertificate(origin: string | null, pem: unknown): string | null {
  if (!origin || typeof pem !== 'string' || pem.length > 16384) return null;
  const host = new URL(origin).hostname;
  if (!LOOPBACK.has(host)) return null;
  try {
    const certificate = new X509Certificate(pem);
    const named = isIP(host.replace(/^\[|\]$/g, '')) ? certificate.checkIP(host.replace(/^\[|\]$/g, '')) : certificate.checkHost(host);
    return named ? certificate.toString() : null;
  } catch { return null; }
}

export function readEmbeddedCertificate(file: string, origin: string | null): string | null {
  try { return embeddedCertificate(origin, (JSON.parse(readFileSync(file, 'utf8')) as { certificate?: unknown }).certificate); } catch { return null; }
}

/**
 * Server used for sign-in and remote analysis.
 * An embedded origin is authoritative in a packaged app, so an environment variable cannot redirect sign-in.
 * Development runs may override it; a package built without one keeps the GITVIZ_SERVER_ORIGIN variable (staging VM).
 */
export function serverOrigin(packaged: boolean, embedded: string | null, override: string | undefined): string | null {
  const fromEnvironment = validOrigin(override);
  if (embedded && packaged) return embedded;
  return fromEnvironment ?? embedded;
}
