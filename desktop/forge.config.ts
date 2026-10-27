import { resolve } from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
const verification = process.env.GITVIZ_VERIFY_PACKAGE === '1';
const p03 = process.env.GITVIZ_VERIFY_P03 === '1';
const p05 = process.env.GITVIZ_VERIFY_P05 === '1';
const p06 = process.env.GITVIZ_VERIFY_P06 === '1';
const p07 = process.env.GITVIZ_VERIFY_P07 === '1';
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
  makers: [], publishers: [], plugins: [],
};
if (p06 || p07) {
  config.packagerConfig.name = p07 ? 'GitVizWorkspaceP07Verify' : 'GitVizWorkspaceP06Verify';
  config.packagerConfig.executableName = p07 ? 'GitVizWorkspaceP07Verify' : 'GitVizWorkspaceP06Verify';
  config.packagerConfig.afterCopy = [async ({ buildPath }: { buildPath: string }) => {
    const path = resolve(buildPath, 'package.json');
    const manifest = JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>;
    manifest.main = p07 ? 'dist/tests/validation.e2e.js' : 'dist/tests/refactor-package.e2e.js';
    manifest.name = 'gitviz-workspace-verification';
    await writeFile(path, JSON.stringify(manifest));
  }];
}
export default config;
