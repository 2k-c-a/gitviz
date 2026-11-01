// Bundled by build.mjs into dist/ui/web/js/collab-editor.bundle.js (IIFE, global GitVizCollab) for the app only.
// Real-time co-editing (AD-29): CodeMirror 6 editor bound to a Yjs document with remote cursors via awareness.
import { basicSetup } from 'codemirror';
import { EditorState, Compartment } from '@codemirror/state';
import { EditorView, keymap } from '@codemirror/view';
import { indentWithTab } from '@codemirror/commands';
import { javascript } from '@codemirror/lang-javascript';
import { python } from '@codemirror/lang-python';
import { json } from '@codemirror/lang-json';
import { markdown } from '@codemirror/lang-markdown';
import { css } from '@codemirror/lang-css';
import { html } from '@codemirror/lang-html';
import * as Y from 'yjs';
import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate, removeAwarenessStates } from 'y-protocols/awareness';
import { yCollab } from 'y-codemirror.next';

const LANGUAGES = {
  javascript: () => javascript(), typescript: () => javascript({ typescript: true }), jsx: () => javascript({ jsx: true }),
  tsx: () => javascript({ jsx: true, typescript: true }), python: () => python(), json: () => json(), markdown: () => markdown(),
  css: () => css(), html: () => html(),
};

/** Remote edits and cursors carry this origin so they are not sent back out. */
export const REMOTE = 'remote';

export function createCollabEditor({ parent, text = null, language = 'plain', user, readOnly = false }) {
  const doc = new Y.Doc();
  const ytext = doc.getText('code');
  // Only the host seeds the document; guests start empty and fill from the host's state.
  if (typeof text === 'string' && text) ytext.insert(0, text);
  const awareness = new Awareness(doc);
  awareness.setLocalStateField('user', { name: user.name, color: user.color, colorLight: `${user.color}33` });
  const editable = new Compartment();
  const extensions = [basicSetup, keymap.of([indentWithTab]), EditorView.lineWrapping, yCollab(ytext, awareness),
    editable.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)])];
  if (LANGUAGES[language]) extensions.push(LANGUAGES[language]());
  const view = new EditorView({ parent, state: EditorState.create({ doc: ytext.toString(), extensions }) });
  return {
    doc, ytext, awareness, view,
    setReadOnly(value) { view.dispatch({ effects: editable.reconfigure([EditorState.readOnly.of(value), EditorView.editable.of(!value)]) }); },
    destroy() { removeAwarenessStates(awareness, [doc.clientID], 'local'); awareness.destroy(); view.destroy(); doc.destroy(); },
  };
}

export const encodeStateVector = doc => Y.encodeStateVector(doc);
export const encodeStateAsUpdate = (doc, vector) => Y.encodeStateAsUpdate(doc, vector);
export const applyUpdate = (doc, update) => Y.applyUpdate(doc, update, REMOTE);
export const mergeUpdates = updates => Y.mergeUpdates(updates);
export const encodeAwareness = (awareness, clients) => encodeAwarenessUpdate(awareness, clients);
export const applyAwareness = (awareness, update) => applyAwarenessUpdate(awareness, update, REMOTE);
export const removeAwareness = (awareness, clients) => removeAwarenessStates(awareness, clients, REMOTE);
export const languages = Object.keys(LANGUAGES);
