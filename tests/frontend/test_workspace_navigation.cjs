const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');

(async () => {
  const { workspaceEntryUrl, readWorkspaceEntry, isSettingsEntry, SETTINGS_ENTRY_URL } = await import(pathToFileURL(resolve(__dirname, '../../frontend/js/platform/workspace-navigation.js')));
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
  // AI 키는 앱 설정에만 있다(AD-33): 설정 화면으로 가는 주소는 정확히 하나이고 다른 값을 싣지 않는다.
  assert.equal(isSettingsEntry(SETTINGS_ENTRY_URL), true);
  assert.equal(readWorkspaceEntry(SETTINGS_ENTRY_URL), null);
  for (const raw of [SETTINGS_ENTRY_URL + '&projectId=x', SETTINGS_ENTRY_URL + '#ai', 'gitviz://app/web/index.html?view=settings',
    'https://app/index.html?view=settings', 'gitviz://app/index.html?view=settings&view=settings']) assert.equal(isSettingsEntry(raw), false, raw);
  console.log('PASS workspace navigation: same app documents, explicit IDs, no implicit connection, no native action');
})().catch(error => { console.error(error); process.exitCode = 1; });
