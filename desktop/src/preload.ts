// This is compiled separately as one sandbox-compatible CommonJS file.
import { contextBridge, ipcRenderer } from 'electron';
contextBridge.exposeInMainWorld('desktop', Object.freeze({
  auth: Object.freeze({ start: () => ipcRenderer.invoke('desktop:auth-start'), status: () => ipcRenderer.invoke('desktop:auth-status'), logout: () => ipcRenderer.invoke('desktop:auth-logout') }),
  remote: Object.freeze({ request: (options: unknown) => ipcRenderer.invoke('desktop:remote', options) }),
  update: Object.freeze({ status: () => ipcRenderer.invoke('desktop:update-status') }),
  // AI analysis (BYOK): which providers have a saved key. Booleans only; main attaches the key itself.
  aiKeys: Object.freeze({ status: () => ipcRenderer.invoke('desktop:ai-key-status') }),
  // Community chat live events (AD-27). The listener gets only the cloned event object.
  community: Object.freeze({
    subscribe: (options: unknown, listener: unknown) => {
      if (typeof listener !== 'function') return Promise.reject(new Error('INVALID_REQUEST'));
      ipcRenderer.removeAllListeners('desktop:community-event');
      ipcRenderer.on('desktop:community-event', (_event, value: unknown) => { (listener as (value: unknown) => void)(value); });
      return ipcRenderer.invoke('desktop:community-subscribe', options);
    },
    unsubscribe: () => { ipcRenderer.removeAllListeners('desktop:community-event'); return ipcRenderer.invoke('desktop:community-unsubscribe'); },
  }),
  // Co-editing (AD-29): the host's file is picked in a native dialog; the page sees a handle and text, never a path.
  collab: Object.freeze({
    open: () => ipcRenderer.invoke('desktop:collab-open'),
    save: (options: unknown) => ipcRenderer.invoke('desktop:collab-save', options),
    close: (options: unknown) => ipcRenderer.invoke('desktop:collab-close', options),
    // File sets (AD-34): an opaque bundle id and root-relative paths only; absolute paths stay in main.
    openBundle: () => ipcRenderer.invoke('desktop:collab-bundle-open'),
    widenBundle: (options: unknown) => ipcRenderer.invoke('desktop:collab-bundle-widen', options),
    addToBundle: (options: unknown) => ipcRenderer.invoke('desktop:collab-bundle-add', options),
    scanBundle: (options: unknown) => ipcRenderer.invoke('desktop:collab-bundle-scan', options),
    approveInBundle: (options: unknown) => ipcRenderer.invoke('desktop:collab-bundle-approve', options),
    saveBundle: (options: unknown) => ipcRenderer.invoke('desktop:collab-bundle-save', options),
    createInBundle: (options: unknown) => ipcRenderer.invoke('desktop:collab-bundle-create', options),
    runtimes: (options: unknown) => ipcRenderer.invoke('desktop:collab-run-runtimes', options ?? {}),
    run: (options: unknown) => ipcRenderer.invoke('desktop:collab-run', options),
    stopRun: () => ipcRenderer.invoke('desktop:collab-run-stop'),
    closeBundle: (options: unknown) => ipcRenderer.invoke('desktop:collab-bundle-close', options),
    // Sandboxed preview window of the shared HTML/CSS/JS (AD-34).
    openPreview: (options: unknown) => ipcRenderer.invoke('desktop:collab-preview-open', options),
    updatePreview: (options: unknown) => ipcRenderer.invoke('desktop:collab-preview-update', options),
    closePreview: (options: unknown) => ipcRenderer.invoke('desktop:collab-preview-close', options),
  }),
}));
if (location.protocol === 'gitviz:' && location.host === 'app' && location.pathname === '/index.html') contextBridge.exposeInMainWorld('workspace', Object.freeze({
  exportDiagnostics: () => ipcRenderer.invoke('workspace:export-diagnostics'),
  connectRemote: (options: unknown) => ipcRenderer.invoke('workspace:connect-remote', options),
  getRemoteConnection: (options: unknown) => ipcRenderer.invoke('workspace:remote-connection', options),
  openRemoteAnalysis: (options: unknown) => ipcRenderer.invoke('workspace:open-remote-analysis', options),
  getHealth: () => ipcRenderer.invoke('workspace:health'),
  selectAndRegister: () => ipcRenderer.invoke('workspace:select-project'),
  listProjects: (options: unknown) => ipcRenderer.invoke('workspace:list-projects', options),
  getProject: (options: unknown) => ipcRenderer.invoke('workspace:get-project', options),
  updateSettings: (options: unknown) => ipcRenderer.invoke('workspace:update-settings', options),
  unregisterProject: (options: unknown) => ipcRenderer.invoke('workspace:unregister-project', options),
  getGitStatus: (options: unknown) => ipcRenderer.invoke('workspace:git-status', options),
  // One working-tree directory: root-relative names only, never absolute paths or file contents.
  getFileTree: (options: unknown) => ipcRenderer.invoke('workspace:file-tree', options),
  previewReview: (options: unknown) => ipcRenderer.invoke('workspace:review-preview', options),
  startReview: (options: unknown) => ipcRenderer.invoke('workspace:review-start', options),
  getReview: (options: unknown) => ipcRenderer.invoke('workspace:review-get', options),
  listReviews: (options: unknown) => ipcRenderer.invoke('workspace:review-list', options),
  cancelReview: (options: unknown) => ipcRenderer.invoke('workspace:review-cancel', options),
  feedbackReview: (options: unknown) => ipcRenderer.invoke('workspace:review-feedback', options),
  // Saved keys are write-only from the renderer: status returns booleans, never key text.
  getAiKeyStatus: () => ipcRenderer.invoke('workspace:ai-keys-status'),
  saveAiKey: (options: unknown) => ipcRenderer.invoke('workspace:ai-key-save', options),
  deleteAiKey: (options: unknown) => ipcRenderer.invoke('workspace:ai-key-delete', options),
  createProposal: (options: unknown) => ipcRenderer.invoke('workspace:proposal-create', options),
  getProposal: (options: unknown) => ipcRenderer.invoke('workspace:proposal-get', options),
  rejectProposal: (options: unknown) => ipcRenderer.invoke('workspace:proposal-reject', options),
  requestProposalRevision: (options: unknown) => ipcRenderer.invoke('workspace:proposal-revision', options),
  approveProposal: (options: unknown) => ipcRenderer.invoke('workspace:proposal-approve', options),
  applyProposal: (options: unknown) => ipcRenderer.invoke('workspace:proposal-apply', options),
  recoverProposal: (options: unknown) => ipcRenderer.invoke('workspace:proposal-recover', options),
  listRecoveryProposals: (options: unknown) => ipcRenderer.invoke('workspace:proposal-list-recovery', options),
  analyzeImpact: (options: unknown) => ipcRenderer.invoke('workspace:impact-analyze', options),
  getImpact: (options: unknown) => ipcRenderer.invoke('workspace:impact-get', options),
  captureChanges: (options: unknown) => ipcRenderer.invoke('workspace:capture-changes', options),
  previewFile: (options: unknown) => ipcRenderer.invoke('workspace:preview-file', options),
  detectValidation: (options: unknown) => ipcRenderer.invoke('workspace:detect-validation', options),
  runValidation: (options: unknown) => ipcRenderer.invoke('workspace:run-validation', options),
  getValidation: (options: unknown) => ipcRenderer.invoke('workspace:get-validation', options),
  cancelValidation: (options: unknown) => ipcRenderer.invoke('workspace:cancel-validation', options),
  isValidationTrusted: (options: unknown) => ipcRenderer.invoke('workspace:validation-trusted', options),
  createComparison: (options: unknown) => ipcRenderer.invoke('workspace:create-comparison', options),
  getComparison: (options: unknown) => ipcRenderer.invoke('workspace:get-comparison', options),
  setEditor: (options: unknown) => ipcRenderer.invoke('workspace:set-editor', options),
  configureEditor: (options: unknown) => ipcRenderer.invoke('workspace:configure-editor', options),
  copyLocation: (options: unknown) => ipcRenderer.invoke('workspace:copy-location', options),
  openEditor: (options: unknown) => ipcRenderer.invoke('workspace:open-editor', options),
  planValidation: (options: unknown) => ipcRenderer.invoke('workspace:plan-validation', options),
  listNotifications: (options: unknown) => ipcRenderer.invoke('workspace:list-notifications', options),
  markNotificationRead: (options: unknown) => ipcRenderer.invoke('workspace:mark-notification-read', options),
  // The listener receives only the cloned signal object, never the IPC event or sender.
  subscribeEvents: (options: unknown, listener: unknown) => {
    if (typeof listener !== 'function') return Promise.reject(new Error('INVALID_REQUEST'));
    ipcRenderer.removeAllListeners('workspace:event');
    ipcRenderer.on('workspace:event', (_event, signal: unknown) => { (listener as (value: unknown) => void)(signal); });
    return ipcRenderer.invoke('workspace:events-subscribe', options);
  },
  unsubscribeEvents: () => { ipcRenderer.removeAllListeners('workspace:event'); return ipcRenderer.invoke('workspace:events-unsubscribe', {}); },
}));
