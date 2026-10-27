import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

test('sandbox preload imports only electron and exposes named capabilities without raw invoke', () => {
  const preload = readFileSync(join(__dirname, '../preload/preload.js'), 'utf8');
  const names = [...preload.matchAll(/require\(["']([^"']+)["']\)/g)].map(match => match[1]);
  assert.deepEqual(names, ['electron']);
  assert.doesNotMatch(preload, /exposeInMainWorld\(['"](?:ipcRenderer|fs|process)/);
  assert.match(preload, /getHealth/);
  for (const name of ['selectAndRegister', 'listProjects', 'getProject', 'updateSettings', 'unregisterProject', 'getGitStatus', 'captureChanges', 'previewFile', 'detectValidation', 'planValidation', 'previewReview', 'startReview', 'getReview', 'listReviews', 'cancelReview', 'feedbackReview', 'createProposal', 'getProposal', 'rejectProposal', 'requestProposalRevision', 'approveProposal', 'applyProposal', 'recoverProposal', 'listRecoveryProposals', 'analyzeImpact', 'getImpact']) assert.match(preload, new RegExp(name));
  assert.doesNotMatch(preload, /\binvoke\s*:\s*/);
});
