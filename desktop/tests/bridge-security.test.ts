import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { BrowserWindow, IpcMainInvokeEvent } from 'electron';
import { workspaceSender } from '../src/ipc';

test('workspace sender is restricted to the top-level workspace document', () => {
  const frame = { url: '' };
  const contents = { mainFrame: frame };
  const window = { isDestroyed: () => false, webContents: contents } as unknown as BrowserWindow;
  const event = { sender: contents, senderFrame: frame } as unknown as IpcMainInvokeEvent;
  for (const url of ['gitviz://app/web/pages/timeline.html', 'gitviz://app/web/index.html',
    'gitviz://app/index.html#x', 'gitviz://app/web/../index.html', 'gitviz://other/index.html', 'https://app/index.html']) {
    frame.url = url;
    assert.equal(workspaceSender(event, window), false, url);
  }
  for (const url of ['gitviz://app/index.html?x=1']) {
    frame.url = url;
    assert.equal(workspaceSender(event, window), true, url);
  }
  assert.equal(workspaceSender({ ...event, senderFrame: { url: frame.url } } as IpcMainInvokeEvent, window), false);
  assert.equal(workspaceSender(event, { ...window, isDestroyed: () => true } as BrowserWindow), false);
});

test('sandbox preload imports only electron and exposes named capabilities without raw invoke', () => {
  const preload = readFileSync(join(__dirname, '../preload/preload.js'), 'utf8');
  const names = [...preload.matchAll(/require\(["']([^"']+)["']\)/g)].map(match => match[1]);
  assert.deepEqual(names, ['electron']);
  assert.doesNotMatch(preload, /exposeInMainWorld\(['"](?:ipcRenderer|fs|process)/);
  assert.match(preload, /getHealth/);
  assert.match(preload, /desktop:update-status/);
  for (const name of ['selectAndRegister', 'listProjects', 'getProject', 'updateSettings', 'unregisterProject', 'getGitStatus', 'captureChanges', 'previewFile', 'detectValidation', 'planValidation', 'previewReview', 'startReview', 'getReview', 'listReviews', 'cancelReview', 'feedbackReview', 'createProposal', 'getProposal', 'rejectProposal', 'requestProposalRevision', 'approveProposal', 'applyProposal', 'recoverProposal', 'listRecoveryProposals', 'analyzeImpact', 'getImpact', 'listNotifications', 'markNotificationRead', 'subscribeEvents', 'unsubscribeEvents']) assert.match(preload, new RegExp(name));
  assert.doesNotMatch(preload, /\binvoke\s*:\s*/);
});
