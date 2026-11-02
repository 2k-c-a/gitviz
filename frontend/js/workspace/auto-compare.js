// Before/after evidence for the one-button apply: metrics always, tests only for a trusted project with a linked tool.
const api = window.workspace;
const candidates = new Map(); // projectId -> candidateId the user linked in the tests tab
const finished = new Map();   // snapshotId -> runId of a completed validation
window.addEventListener('workspace-validation-candidate', event => candidates.set(event.detail.projectId, event.detail.candidateId));
window.addEventListener('workspace-validation-finished', event => { if (event.detail.status !== 'running') finished.set(event.detail.snapshotId, event.detail.runId); });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

/** Runs the linked tool on a snapshot without a prompt; null when the project is not trusted or nothing is linked. */
async function validate(projectId, snapshotId) {
  const candidateId = candidates.get(projectId);
  if (!candidateId || !await api.isValidationTrusted({ projectId })) return null;
  const plan = await api.planValidation({ projectId, snapshotId, candidateId, idempotencyKey: crypto.randomUUID() });
  let run = await api.runValidation({ projectId, planId: plan.planId, idempotencyKey: crypto.randomUUID() });
  if (!run) return null;
  while (run.status === 'running') { await pause(500); run = await api.getValidation({ projectId, runId: run.runId, offset: 0, limit: 10 }); }
  window.dispatchEvent(new CustomEvent('workspace-validation-finished', { detail: run }));
  return run.runId;
}
/** Baseline test run for the proposal's input, reused when one already finished. Failures only drop the test part. */
export async function baseline(projectId, snapshotId) {
  if (finished.has(snapshotId)) return finished.get(snapshotId);
  try { return await validate(projectId, snapshotId); } catch { return null; }
}
/** Captures the applied files again and compares them with the proposal's input. */
export async function compareAfterApply(proposal, beforeRunId) {
  const { projectId } = proposal;
  const paths = new Set(proposal.changes.map(change => change.path));
  const state = await api.getGitStatus({ projectId, recentLimit: 1 });
  const items = [];
  for (const facet of ['unstaged', 'staged', 'untracked']) for (const file of state[facet]) {
    if (paths.has(file.path) && !items.some(item => item.path === file.path)) items.push({ facet, path: file.path });
  }
  if (!items.length) throw new Error('NO_CHANGES_AFTER_APPLY');
  const after = await api.captureChanges({ projectId, items, includeUntracked: items.some(item => item.facet === 'untracked'), idempotencyKey: crypto.randomUUID() });
  window.dispatchEvent(new CustomEvent('workspace-snapshot-changed', { detail: after }));
  let afterRunId = null;
  if (beforeRunId) { try { afterRunId = await validate(projectId, after.snapshotId); } catch { afterRunId = null; } }
  const value = await api.createComparison({ projectId, beforeSnapshotId: proposal.snapshotId, afterSnapshotId: after.snapshotId,
    proposalId: proposal.proposalId, beforeRunId: afterRunId ? beforeRunId : null, afterRunId, idempotencyKey: crypto.randomUUID() });
  window.dispatchEvent(new CustomEvent('workspace-comparison-ready', { detail: { projectId, value } }));
  return value;
}
