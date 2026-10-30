/* Navigation hints never authorize a local project connection or a remote request. */
const pages = new Set(['/web/index.html', ...['overview', 'timeline', 'network', 'code-analysis', 'search', 'myprojects', 'history', 'ai-review', 'settings'].map(name => `/web/pages/${name}.html`)]);
function appUrl(raw) {
  try {
    const url = new URL(raw);
    return url.protocol === 'gitviz:' && url.host === 'app' && !url.username && !url.password && !url.hash &&
      url.search.length <= 4096 && url.href === raw ? url : null;
  } catch { return null; }
}
function id(params, key) {
  const values = params.getAll(key);
  if (!values.length) return null;
  if (values.length !== 1 || !/^[1-9]\d*$/.test(values[0])) throw new Error('INVALID_REQUEST');
  const value = Number(values[0]);
  if (!Number.isSafeInteger(value)) throw new Error('INVALID_REQUEST');
  return value;
}
export function workspaceEntryUrl(raw) {
  const url = appUrl(raw);
  if (!url || !pages.has(url.pathname)) return null;
  try {
    const repository = id(url.searchParams, 'repo');
    const snapshot = id(url.searchParams, 'snapshot');
    if (snapshot !== null && repository === null) return null;
    const target = new URL('gitviz://app/index.html?view=inbox');
    if (repository !== null) target.searchParams.set('remoteRepositoryId', String(repository));
    if (snapshot !== null) target.searchParams.set('remoteSnapshotId', String(snapshot));
    return target.href;
  } catch { return null; }
}
export function readWorkspaceEntry(raw) {
  const url = appUrl(raw);
  if (!url || url.pathname !== '/index.html' || url.searchParams.getAll('view').length !== 1 || url.searchParams.get('view') !== 'inbox' ||
    [...url.searchParams.keys()].some(key => !['view', 'remoteRepositoryId', 'remoteSnapshotId'].includes(key))) return null;
  try {
    const repositoryId = id(url.searchParams, 'remoteRepositoryId');
    const snapshotId = id(url.searchParams, 'remoteSnapshotId');
    return snapshotId !== null && repositoryId === null ? null : { repositoryId, snapshotId };
  } catch { return null; }
}
