/**
 * Packaged releases must never expose a debugging port. Chromium (`--remote-debugging-port`, `-pipe`, `-address`, `-socket-name`)
 * and Node (`--inspect*`) accept these from the command line of any process that can start the exe, so the release refuses to run with them.
 * Development (unpackaged) runs are unaffected, and no other launch argument is looked at.
 */
export const DEBUG_EXIT_CODE = 2;

// Chromium parses switches after "--", "-" or "/" (Windows) and is case-insensitive about the name; the value follows "=" or is absent.
const DEBUG_SWITCH = /^(?:--?|\/)(?:remote-debugging-[a-z-]*|inspect(?:-[a-z-]*)?|debug-port|debugger-agent)(?:=|$)/i;

export function debugSwitches(argv: readonly string[]): string[] {
  return argv.filter(argument => DEBUG_SWITCH.test(argument.trim())).map(argument => argument.trim().split('=')[0]);
}

/** Names of the debugging switches that must stop this process, or none when it is a development run. */
export function forbiddenDebugSwitches(packaged: boolean, argv: readonly string[]): string[] {
  return packaged ? debugSwitches(argv) : [];
}
