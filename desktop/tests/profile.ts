/** Fixture-only Chromium/OSCrypt profile. Never use a real app session in E2E. */
import { app } from 'electron';
import { mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

export const profileDirectory = mkdtempSync(join(tmpdir(), 'gitviz-electron-fixture-'));
app.setPath('userData', profileDirectory);
app.setPath('sessionData', profileDirectory);
app.setPath('crashDumps', profileDirectory);
