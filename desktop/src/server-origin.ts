import { readFileSync } from 'node:fs';

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
