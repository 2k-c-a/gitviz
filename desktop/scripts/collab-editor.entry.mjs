// Bundled by build.mjs into dist/ui/web/js/collab-editor.bundle.js (IIFE, global GitVizCollab) for the app only.
// Real-time co-editing: CodeMirror 6 editors bound to Yjs documents with remote cursors via awareness.
// A file set (AD-34) is one "manifest" document (which files and images/fonts exist, which file to preview) plus one
// document per text file, so each file syncs on its own and stays within one relay message.
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

/** The set's file list: files (path → language), assets (path → { sha256, size, mime }), meta (entry, pending). Only the host writes it. */
export function createManifest() {
  const doc = new Y.Doc();
  return { doc, files: doc.getMap('files'), assets: doc.getMap('assets'), meta: doc.getMap('meta') };
}

/** One shared text file. Only the host seeds the text; guests start empty and fill from the host's state. */
export function createFile(text = null) {
  const doc = new Y.Doc();
  const ytext = doc.getText('code');
  if (typeof text === 'string' && text) ytext.insert(0, text);
  return { doc, ytext, awareness: new Awareness(doc) };
}

/** An editor view on one file. The file (document and awareness) outlives the view: switching files only swaps views. */
export function createEditor({ parent, file, language = 'plain', readOnly = false }) {
  const editable = new Compartment();
  const extensions = [basicSetup, keymap.of([indentWithTab]), EditorView.lineWrapping, yCollab(file.ytext, file.awareness),
    editable.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)])];
  if (LANGUAGES[language]) extensions.push(LANGUAGES[language]());
  const view = new EditorView({ parent, state: EditorState.create({ doc: file.ytext.toString(), extensions }) });
  return {
    view,
    setReadOnly(value) { view.dispatch({ effects: editable.reconfigure([EditorState.readOnly.of(value), EditorView.editable.of(!value)]) }); },
    destroy() { view.destroy(); },
  };
}

/** Shows (user) or hides (null) my cursor in one file; hiding sends a removal to the others. */
export function setPresence(awareness, user) {
  // setLocalStateField does nothing once the state is null, so set the whole state.
  if (user) awareness.setLocalState({ ...(awareness.getLocalState() ?? {}), user: { name: user.name, color: user.color, colorLight: `${user.color}33` } });
  else awareness.setLocalState(null);
}
export function destroyFile(file) { removeAwarenessStates(file.awareness, [file.doc.clientID], 'local'); file.awareness.destroy(); file.doc.destroy(); }

export const encodeStateVector = doc => Y.encodeStateVector(doc);
export const encodeStateAsUpdate = (doc, vector) => Y.encodeStateAsUpdate(doc, vector);
export const applyUpdate = (doc, update) => Y.applyUpdate(doc, update, REMOTE);
export const mergeUpdates = updates => Y.mergeUpdates(updates);
export const encodeAwareness = (awareness, clients) => encodeAwarenessUpdate(awareness, clients);
export const applyAwareness = (awareness, update) => applyAwarenessUpdate(awareness, update, REMOTE);
export const removeAwareness = (awareness, clients) => removeAwarenessStates(awareness, clients, REMOTE);
export const languages = Object.keys(LANGUAGES);
