import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { embeddedCertificate, readEmbeddedCertificate, readEmbeddedOrigin, serverOrigin, validOrigin } from '../src/server-origin';

test('only bare https origins are accepted', () => {
  assert.equal(validOrigin('https://gitviz.example'), 'https://gitviz.example');
  assert.equal(validOrigin('https://localhost:8443'), 'https://localhost:8443');
  for (const value of ['http://gitviz.example', 'https://gitviz.example/', 'https://gitviz.example/api', 'https://user:pw@gitviz.example', 'https://gitviz.example?x=1', 'gitviz.example', '', null, 42]) assert.equal(validOrigin(value), null, String(value));
});
test('embedded origin wins in a package; environment overrides only in development or when nothing is embedded', () => {
  const embedded = 'https://gitviz.example', other = 'https://attacker.example';
  assert.equal(serverOrigin(true, embedded, other), embedded);
  assert.equal(serverOrigin(true, embedded, undefined), embedded);
  assert.equal(serverOrigin(false, embedded, other), other);
  assert.equal(serverOrigin(false, embedded, 'http://bad'), embedded);
  assert.equal(serverOrigin(true, null, 'https://localhost:8443'), 'https://localhost:8443');
  assert.equal(serverOrigin(true, null, 'not a url'), null);
  assert.equal(serverOrigin(true, null, undefined), null);
});
test('embedded config file is read defensively', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-origin-'));
  try {
    const file = join(directory, 'server.json');
    assert.equal(readEmbeddedOrigin(file), null);
    await writeFile(file, JSON.stringify({ origin: 'https://gitviz.example' })); assert.equal(readEmbeddedOrigin(file), 'https://gitviz.example');
    await writeFile(file, JSON.stringify({ origin: null })); assert.equal(readEmbeddedOrigin(file), null);
    await writeFile(file, JSON.stringify({ origin: 'http://gitviz.example' })); assert.equal(readEmbeddedOrigin(file), null);
    await writeFile(file, 'corrupt'); assert.equal(readEmbeddedOrigin(file), null);
  } finally { await rm(directory, { recursive: true }); }
});
// Fixed test certificates (public part only): one names localhost and 127.0.0.1, the other only other.test.
const LOCAL = `-----BEGIN CERTIFICATE-----
MIIBODCB36ADAgECAgEBMAoGCCqGSM49BAMCMBYxFDASBgNVBAMMC0dpdFZpeiB0
ZXN0MB4XDTI2MDEwMTAwMDAwMFoXDTM1MTIzMDAwMDAwMFowFjEUMBIGA1UEAwwL
R2l0Vml6IHRlc3QwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAASTp5WeKWPeDBMp
pnfdVEJMh97YpdMTKTlTu7fz06qZfkrqYKCsXbp0GtJPhkNpGHpjgiI/+bxOdhv8
iT1NLmehox4wHDAaBgNVHREEEzARgglsb2NhbGhvc3SHBH8AAAEwCgYIKoZIzj0E
AwIDSAAwRQIgG4eQ5D3Eodl36EWAXe4Sr3z4YBTlsPFsCMHr947yXJoCIQDMAV2l
yoOMfsPemvbNgFSWkjQkoMltRr/10AYaaGdlzA==
-----END CERTIFICATE-----
`;
const OTHER = `-----BEGIN CERTIFICATE-----
MIIBNDCB2qADAgECAgEBMAoGCCqGSM49BAMCMBYxFDASBgNVBAMMC0dpdFZpeiB0
ZXN0MB4XDTI2MDEwMTAwMDAwMFoXDTM1MTIzMDAwMDAwMFowFjEUMBIGA1UEAwwL
R2l0Vml6IHRlc3QwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQwc0gqgppRX/7j
XcPtbbqLLSrqKWdbXvivJ9MQBPTtc6Ccs57mLjZGasRr9LygwwUckoXTza7y/kQV
fXDyFqdsoxkwFzAVBgNVHREEDjAMggpvdGhlci50ZXN0MAoGCCqGSM49BAMCA0kA
MEYCIQDHwAeCG47ShNgD5+e133byGS3XZT1IuFXhuA5UgeNtEAIhANl1F28w4QWD
+ngWxYij+m1yEYO8Mx3p17QfbsB92zLW
-----END CERTIFICATE-----
`;
test('an embedded certificate is trusted only for a loopback origin it names', async () => {
  assert.ok(embeddedCertificate('https://localhost:8443', LOCAL)?.includes('BEGIN CERTIFICATE'));
  assert.ok(embeddedCertificate('https://127.0.0.1:8443', LOCAL));
  // A real server origin never takes an embedded certificate, even one that names it.
  assert.equal(embeddedCertificate('https://other.test', OTHER), null);
  assert.equal(embeddedCertificate('https://localhost:8443', OTHER), null);
  for (const bad of [null, 42, '', 'not a certificate', `${LOCAL}x`.repeat(200)]) assert.equal(embeddedCertificate('https://localhost:8443', bad), null);
  assert.equal(embeddedCertificate(null, LOCAL), null);
  const directory = await mkdtemp(join(tmpdir(), 'gitviz-origin-'));
  try {
    const file = join(directory, 'server.json');
    assert.equal(readEmbeddedCertificate(file, 'https://localhost:8443'), null);
    await writeFile(file, JSON.stringify({ origin: 'https://localhost:8443', certificate: LOCAL }));
    assert.ok(readEmbeddedCertificate(file, 'https://localhost:8443'));
    await writeFile(file, JSON.stringify({ origin: 'https://localhost:8443' })); assert.equal(readEmbeddedCertificate(file, 'https://localhost:8443'), null);
    await writeFile(file, 'corrupt'); assert.equal(readEmbeddedCertificate(file, 'https://localhost:8443'), null);
  } finally { await rm(directory, { recursive: true }); }
});
