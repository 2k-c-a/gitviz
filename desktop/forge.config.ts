import { resolve } from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
const verification = process.env.GITVIZ_VERIFY_PACKAGE === '1';
const p03 = process.env.GITVIZ_VERIFY_P03 === '1';
const p05 = process.env.GITVIZ_VERIFY_P05 === '1';
const p06 = process.env.GITVIZ_VERIFY_P06 === '1';
const p07 = process.env.GITVIZ_VERIFY_P07 === '1';
const p08b = process.env.GITVIZ_VERIFY_P08B === '1';
const p04 = process.env.GITVIZ_VERIFY_P04 === '1';
// Forge validates this config at package time. Importing its umbrella types also
// imports unrelated macOS signing declarations that require newer Node types.
const config = {
  packagerConfig: { name: p05 ? 'GitVizWorkspaceP05Verify' : p04 ? 'GitVizWorkspaceP04Verify' : p03 ? 'GitVizWorkspaceP03Verify' : verification ? 'GitVizWorkspaceVerify' : 'GitVizWorkspace', executableName: p05 ? 'GitVizWorkspaceP05Verify' : p04 ? 'GitVizWorkspaceP04Verify' : p03 ? 'GitVizWorkspaceP03Verify' : verification ? 'GitVizWorkspaceVerify' : 'GitVizWorkspace', asar: true,
    extraResource: [resolve(process.cwd(), '../dist/workspace-agent/agent')],
    ignore: (path: string) => path !== '' && !/^\/(?:dist(?:\/|$)|node_modules(?:\/|$)|package\.json$)/.test(path.replaceAll('\\', '/')),
    afterCopy: (verification || p03 || p04 || p05) ? [async ({ buildPath }: { buildPath: string }) => {
      const path = resolve(buildPath, 'package.json');
      const manifest = JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>;
      manifest.main = p05 ? 'dist/tests/review-preview.e2e.js' : p04 ? 'dist/tests/changes.e2e.js' : p03 ? 'dist/tests/remote-compat.e2e.js' : 'dist/tests/foundation.e2e.js';
      manifest.name = 'gitviz-workspace-verification';
      await writeFile(path, JSON.stringify(manifest));
    }] : [],
  },
  // Per-user install under %LocalAppData% (Q01): no MSI, no Squirrel auto-update. Signing only when both variables are provided.
  makers: [{ name: '@electron-forge/maker-squirrel', config: {
    name: 'GitVizWorkspace', setupExe: 'GitVizWorkspaceSetup.exe', noMsi: true, authors: 'GitViz', description: 'GitViz Windows workspace',
    ...(process.env.GITVIZ_SIGN_PFX && process.env.GITVIZ_SIGN_PFX_PASSWORD ? { certificateFile: process.env.GITVIZ_SIGN_PFX, certificatePassword: process.env.GITVIZ_SIGN_PFX_PASSWORD } : {}),
  } }], publishers: [], plugins: [],
};
if (p06 || p07 || p08b) {
  config.packagerConfig.name = p08b ? 'GitVizWorkspaceP08bVerify' : p07 ? 'GitVizWorkspaceP07Verify' : 'GitVizWorkspaceP06Verify';
  config.packagerConfig.executableName = p08b ? 'GitVizWorkspaceP08bVerify' : p07 ? 'GitVizWorkspaceP07Verify' : 'GitVizWorkspaceP06Verify';
  config.packagerConfig.afterCopy = [async ({ buildPath }: { buildPath: string }) => {
    const path = resolve(buildPath, 'package.json');
    const manifest = JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>;
    manifest.main = p08b ? 'dist/tests/remote-workspace.e2e.js' : p07 ? 'dist/tests/validation.e2e.js' : 'dist/tests/refactor-package.e2e.js';
    manifest.name = 'gitviz-workspace-verification';
    await writeFile(path, JSON.stringify(manifest));
  }];
}
export default config;
