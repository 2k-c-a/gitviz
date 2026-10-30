const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');

(async () => {
  const { workspaceEntryUrl, readWorkspaceEntry } = await import(pathToFileURL(resolve(__dirname, '../../frontend/js/platform/workspace-navigation.js')));
  const source = 'gitviz://app/web/pages/timeline.html?repo=2&snapshot=7&sort_by=name';
  const target = workspaceEntryUrl(source);
  assert.equal(target, 'gitviz://app/index.html?view=inbox&remoteRepositoryId=2&remoteSnapshotId=7');
  assert.deepEqual(readWorkspaceEntry(target), { repositoryId: 2, snapshotId: 7 });
  assert.deepEqual(readWorkspaceEntry(workspaceEntryUrl('gitviz://app/web/index.html')), { repositoryId: null, snapshotId: null });
  for (const raw of ['https://app/web/index.html', 'gitviz://other/web/index.html', 'gitviz://app/web/../web/index.html',
    'gitviz://app/web/index.html#x', 'gitviz://u:p@app/web/index.html', 'gitviz://app/web/pages/missing.html',
    'gitviz://app/web/pages/timeline.html?repo=1&repo=2', 'gitviz://app/web/pages/timeline.html?repo=0',
    'gitviz://app/web/pages/timeline.html?repo=9007199254740992', 'gitviz://app/web/pages/timeline.html?snapshot=7']) assert.equal(workspaceEntryUrl(raw), null, raw);
  for (const suffix of ['view=inbox&view=inbox', 'view=inbox&projectId=forged', 'view=inbox&remoteRepositoryId=1&remoteRepositoryId=2',
    'view=inbox&remoteSnapshotId=7', 'view=inbox&root=C:/secret', 'view=inbox&remoteRepositoryId=01', 'view=apply']) assert.equal(readWorkspaceEntry('gitviz://app/index.html?' + suffix), null, suffix);
  console.log('PASS workspace navigation: same app documents, explicit IDs, no implicit connection, no native action');
})().catch(error => { console.error(error); process.exitCode = 1; });
