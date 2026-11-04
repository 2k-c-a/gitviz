import './profile';
/** Co-editing run (AD-34 phase 2·3): Pyodide in its sandboxed window, and local C/C++/Java under a Job Object, on this PC's tools. */
import assert from 'node:assert/strict';
import { app, BrowserWindow } from 'electron';
import { join } from 'node:path';
import { registerScheme } from '../src/app-protocol';
import { CollabRunner, JOB_TIMEOUT_EXIT, RUN_LIMITS, javaMainClass, jobScript, languageFor, quoteArg, readRequest, type RunResult } from '../src/collab-run';

registerScheme(); app.on('window-all-closed', () => {});
let phase = 'startup';
const deadline = setTimeout(() => { process.stderr.write(`FAIL collab run deadline (${phase})\n`); app.exit(1); }, 240000);
const skipped: string[] = [];

void app.whenReady().then(async () => {
  try {
    phase = 'pure';
    assert.equal(languageFor('a/b.py'), 'python'); assert.equal(languageFor('x.cc'), 'cpp'); assert.equal(languageFor('M.java'), 'java'); assert.equal(languageFor('x.h'), null);
    assert.equal(javaMainClass('src/app/Main.java', '/* package x; */\npackage app;\nclass Main {}'), 'app.Main');
    assert.equal(javaMainClass('Main.java', 'class Main {}'), 'Main');
    assert.equal(quoteArg('a b'), '"a b"'); assert.equal(quoteArg('plain'), 'plain'); assert.equal(quoteArg('a"b'), '"a\\"b"'); assert.equal(quoteArg('dir\\'), 'dir\\');
    for (const bad of [{ entry: '../x.py', files: { '../x.py': '' } }, { entry: 'x.py', files: {} }, { entry: 'x.txt', files: { 'x.txt': '' } },
      { entry: 'x.py', files: { 'x.py': '' }, extra: 1 }, { entry: 'x.py', files: { 'x.py': '', 'X.py': '' } }, { entry: 'x.py', files: { 'x.py': '' }, stdin: 'x'.repeat(70000) }])
      assert.throws(() => readRequest(bad), /INVALID_REQUEST/);

    const runner = new CollabRunner(join(__dirname, '../../node_modules/pyodide'), process.env);
    const runtimes = await runner.runtimes({});
    const run = (entry: string, files: Record<string, string>, stdin = '') => runner.run({ entry, files, stdin }) as Promise<RunResult>;

    phase = 'python';
    const windows = BrowserWindow.getAllWindows().length;
    let result = await run('main.py', {
      'main.py': 'import helper\nfrom pkg.tool import twice\nname = input()\nprint(helper.greet(name), twice(21))\nprint("한글 출력")\n',
      'helper.py': 'def greet(name):\n    return f"hi {name}"\n', 'pkg/__init__.py': '', 'pkg/tool.py': 'def twice(x):\n    return x * 2\n',
    }, 'gitviz\n');
    assert.equal(result.exitCode, 0, result.stderr);
    assert.equal(result.stdout, 'hi gitviz 42\n한글 출력\n');
    assert.equal(BrowserWindow.getAllWindows().length, windows, 'the run window is gone afterwards');
    result = await run('bad.py', { 'bad.py': 'def f():\n    raise ValueError("boom")\nf()\n' });
    assert.equal(result.exitCode, 1);
    assert.match(result.stderr, /File "bad\.py", line 2, in f[\s\S]*ValueError: boom/);
    result = await run('exit.py', { 'exit.py': 'import sys\nprint("x")\nsys.exit(3)\n' });
    assert.deepEqual([result.exitCode, result.stdout], [3, 'x\n']);
    // No network and no access to this PC's files from the sandbox.
    result = await run('net.py', { 'net.py': [
      'import js, os',
      'try:',
      '    from pyodide.http import open_url',
      '    open_url("https://example.org/")',
      '    print("net-open")',
      'except Exception as error:',
      '    print("net-blocked")',
      'print(os.path.exists("C:/Windows"), hasattr(js, "require"), hasattr(js, "desktop"))',
    ].join('\n') });
    assert.equal(result.stdout, 'net-blocked\nFalse False False\n', result.stderr);
    phase = 'python-timeout';
    const loop = await run('loop.py', { 'loop.py': 'while True:\n    pass\n' });
    assert.equal(loop.timedOut, true);
    assert.equal(BrowserWindow.getAllWindows().length, windows);
    phase = 'python-stop';
    const stopping = run('loop.py', { 'loop.py': 'while True:\n    pass\n' });
    await new Promise(resolve => setTimeout(resolve, 3000));
    assert.equal(runner.stop().stopped, true);
    await assert.rejects(stopping, /STOPPED/);
    assert.equal(runner.stop().stopped, false);

    if (runtimes.c) {
      phase = 'c';
      result = await run('src/main.c', { 'src/main.c': '#include <stdio.h>\n#include "util.h"\nint main(void){ int n; if (scanf("%d", &n) != 1) return 2; printf("%d\\n", twice(n)); return 0; }\n',
        'src/util.h': 'int twice(int);\n', 'src/util.c': '#include "util.h"\nint twice(int x){ return x * 2; }\n' }, '21\n');
      assert.equal(result.exitCode, 0, result.stderr); assert.equal(result.stdout.replace(/\r\n/g, '\n'), '42\n'); assert.equal(result.phase, 'run');
      result = await run('a.c', { 'a.c': 'int main(void) { return nope; }\n' });
      assert.equal(result.phase, 'compile'); assert.notEqual(result.exitCode, 0); assert.match(result.stderr, /a\.c:1:.*nope/);
      assert.ok(!result.stderr.includes('gitviz-run-'), 'no temp folder in messages');
      phase = 'c-limits';
      // The time limit is measured inside the job, after PowerShell start-up and the Add-Type compile (slow on a cold machine).
      const script = jobScript('p.exe', '', 'C:\\x', 1, 1, RUN_LIMITS.runSeconds);
      assert.ok(script.indexOf(`WaitForExit(${RUN_LIMITS.runSeconds * 1000})`) > script.indexOf('[GitVizJob]::Enter'));
      assert.ok(script.includes(`exit ${JOB_TIMEOUT_EXIT}`));
      result = await run('spin.c', { 'spin.c': 'int main(void){ for(;;){} }\n' });
      assert.equal(result.timedOut, true); assert.equal(result.exitCode, null);
      assert.ok(!result.stderr.includes('GITVIZ_RUN_TIMEOUT'));
      assert.ok(result.durationMs < (RUN_LIMITS.runSeconds + 30) * 1000, 'the job ends the program, not the safety net');
      // Allocating past the memory limit fails inside the job.
      result = await run('mem.c', { 'mem.c': '#include <stdlib.h>\n#include <string.h>\n#include <stdio.h>\nint main(void){ for (int i = 0; i < 64; i++) { char *p = malloc(64 << 20); if (!p) { puts("limited"); return 0; } memset(p, 1, 64 << 20); } puts("unlimited"); return 0; }\n' });
      assert.equal(result.stdout.trim(), 'limited', `${result.exitCode} ${result.stderr}`);
      // Children beyond the process limit cannot start.
      result = await run('fork.c', { 'fork.c': '#include <stdlib.h>\n#include <stdio.h>\n#include <process.h>\nint main(void){ int ok = 0; for (int i = 0; i < 20; i++) if (_spawnl(_P_NOWAIT, "C:\\\\Windows\\\\System32\\\\PING.EXE", "ping", "-n", "4", "127.0.0.1", NULL) != -1) ok++; printf("count=%d\\n", ok); return 0; }\n' });
      // PowerShell and the program take two of the job's process slots.
      const started = Number(/count=(\d+)/.exec(result.stdout)?.[1]);
      assert.ok(started >= 1 && started <= RUN_LIMITS.processes - 2, `started ${started}`);
      assert.ok(!result.stderr.includes('CLIXML'), 'no PowerShell noise');
      phase = 'c-output';
      result = await run('flood.c', { 'flood.c': '#include <stdio.h>\nint main(void){ for(;;) puts("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"); }\n' });
      assert.equal(result.truncated, true); assert.ok(result.stdout.length <= 200 * 1024);
    } else skipped.push('C');
    if (runtimes.cpp) {
      phase = 'cpp';
      result = await run('main.cpp', { 'main.cpp': '#include <iostream>\n#include <string>\nint main(){ std::string s; std::getline(std::cin, s); std::cout << "C++ " << s << std::endl; }\n' }, 'ok\n');
      assert.equal(result.stdout.replace(/\r\n/g, '\n'), 'C++ ok\n', result.stderr);
    } else skipped.push('C++');
    if (runtimes.java) {
      phase = 'java';
      result = await run('src/app/Main.java', {
        'src/app/Main.java': 'package app;\nimport util.Text;\npublic class Main { public static void main(String[] a) throws Exception { var r = new java.io.BufferedReader(new java.io.InputStreamReader(System.in)); System.out.println(Text.shout(r.readLine()) + " 자바"); } }\n',
        'src/util/Text.java': 'package util;\npublic class Text { public static String shout(String s) { return s.toUpperCase(); } }\n' }, 'hello\n');
      assert.equal(result.exitCode, 0, result.stderr); assert.equal(result.stdout.replace(/\r\n/g, '\n'), 'HELLO 자바\n');
      result = await run('B.java', { 'B.java': 'class B { void f() { int x = "s"; } }\n' });
      assert.equal(result.phase, 'compile'); assert.match(result.stderr, /B\.java:1: error/);
    } else skipped.push('Java');
    process.stdout.write(`PASS collab run: Pyodide sandbox (local modules, stdin, traceback, exit code, no network/files, timeout, stop)${runtimes.c ? ', C (multi-file, compile error, timeout, memory and process limits, output cap)' : ''}${runtimes.cpp ? ', C++' : ''}${runtimes.java ? ', Java (packages, compile error)' : ''}${skipped.length ? `; NOT TESTED (no compiler): ${skipped.join(', ')}` : ''}\n`);
    clearTimeout(deadline); app.exit(0);
  } catch (error) {
    process.stderr.write(`FAIL collab run (${phase}): ${error instanceof Error ? error.stack : String(error)}\n`);
    clearTimeout(deadline); app.exit(1);
  }
});
