import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { BrowserWindow, IpcMainInvokeEvent } from 'electron';
import { workspaceSender } from '../src/ipc';
import { microphoneAllowed } from '../src/window';

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
  assert.match(preload, /desktop:community-subscribe/);
  assert.match(preload, /desktop:community-unsubscribe/);
  for (const channel of ['desktop:collab-open', 'desktop:collab-save', 'desktop:collab-close', 'desktop:collab-bundle-open', 'desktop:collab-bundle-widen',
    'desktop:collab-bundle-add', 'desktop:collab-bundle-scan', 'desktop:collab-bundle-approve', 'desktop:collab-bundle-save', 'desktop:collab-bundle-close',
    'desktop:collab-preview-open', 'desktop:collab-preview-update', 'desktop:collab-preview-close', 'desktop:collab-bundle-create',
    'desktop:collab-run-runtimes', 'desktop:collab-run', 'desktop:collab-run-stop']) assert.match(preload, new RegExp(channel));
  for (const name of ['selectAndRegister', 'listProjects', 'getProject', 'updateSettings', 'unregisterProject', 'getGitStatus', 'captureChanges', 'previewFile', 'detectValidation', 'planValidation', 'previewReview', 'startReview', 'getReview', 'listReviews', 'cancelReview', 'feedbackReview', 'createProposal', 'getProposal', 'rejectProposal', 'requestProposalRevision', 'approveProposal', 'applyProposal', 'recoverProposal', 'listRecoveryProposals', 'analyzeImpact', 'getImpact', 'listNotifications', 'markNotificationRead', 'subscribeEvents', 'unsubscribeEvents', 'getAiKeyStatus', 'saveAiKey', 'deleteAiKey', 'isValidationTrusted']) assert.match(preload, new RegExp(name));
  assert.doesNotMatch(preload, /getAiKey\b|readAiKey/);
  assert.doesNotMatch(preload, /\binvoke\s*:\s*/);
});

test('only the top-level community page may use the microphone, and nothing else', () => {
  const page = 'gitviz://app/web/pages/community.html?id=2&tab=chat';
  assert.equal(microphoneAllowed('media', { mediaTypes: ['audio'], requestingUrl: page, isMainFrame: true }), true);
  assert.equal(microphoneAllowed('media', { mediaType: 'audio', requestingUrl: page }), true);
  for (const [permission, details] of [
    ['media', { mediaTypes: ['audio', 'video'], requestingUrl: page }], ['media', { mediaTypes: ['video'], requestingUrl: page }],
    ['media', { mediaTypes: [], requestingUrl: page }], ['media', { mediaTypes: ['audio'], requestingUrl: page, isMainFrame: false }],
    ['media', { mediaTypes: ['audio'], requestingUrl: 'gitviz://app/web/pages/timeline.html' }], ['media', { mediaTypes: ['audio'], requestingUrl: 'gitviz://app/index.html' }],
    ['media', { mediaTypes: ['audio'], requestingUrl: 'https://evil.example/web/pages/community.html' }], ['media', { mediaTypes: ['audio'] }],
    ['notifications', { requestingUrl: page }], ['display-capture', { requestingUrl: page }], ['geolocation', { requestingUrl: page }],
  ] as const) assert.equal(microphoneAllowed(permission, details as Parameters<typeof microphoneAllowed>[1]), false, `${permission} ${JSON.stringify(details)}`);
});
