import { test, afterAll } from 'vitest';
import ts from 'typescript';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const dir = mkdtempSync(join(tmpdir(), 'clawweb-logging-'));
const modulePath = join(dir, 'console-logging.mjs');
writeFileSync(modulePath, ts.transpileModule(readFileSync(new URL('../console-logging.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText);
const moduleUrl = pathToFileURL(modulePath).href;
const preload = pathToFileURL(join(dir, 'install.mjs'));
writeFileSync(preload, `import { installConsoleLogging } from ${JSON.stringify(moduleUrl)}; installConsoleLogging();`);
afterAll(() => rmSync(dir, { recursive: true, force: true }));
const prefix = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (ERROR|WARN|INFO|DEBUG) /gm;
function run(source: string, enabled = true) {
  const result = spawnSync(process.execPath, [...(enabled ? ['--import', preload.href] : []), '--input-type=module', '-e', source], {
    encoding: 'utf8', timeout: 15_000,
  });
  assert.ifError(result.error);
  return result;
}
function strip(text: string) { return text.replace(prefix, ''); }

for (const [method, level, stream] of [
  ['error', 'ERROR', 'stderr'], ['warn', 'WARN', 'stderr'],
  ['log', 'INFO', 'stdout'], ['info', 'INFO', 'stdout'], ['debug', 'DEBUG', 'stdout'],
]) {
  test(`${method} adds ${level} on its original stream`, () => {
    const result = run(`console.${method}('你好 %s %d', 'world', 42)`);
    assert.equal(result.status, 0);
    assert.match(result[stream], new RegExp(` ${level} 你好 world 42\\n$`));
    assert.equal(strip(result[stream]), '你好 world 42\n');
    assert.equal(result[stream === 'stdout' ? 'stderr' : 'stdout'], '');
  });
}

test('keeps argument formatting, multiline output and existing ERROR text', () => {
  const source = `
    const obj = { key: 'value' }; obj.self = obj;
    const error = new Error('example'); error.stack = 'Error: example\\n    at fixed-stack';
    console.error('[clawweb] ERROR', error, obj);
    console.log(); console.info('INFO mentions ERROR failed status=500');
    console.warn('first\\nsecond');
  `;
  const plain = run(source, false), formatted = run(source);
  assert.equal(formatted.status, 0);
  assert.equal(strip(formatted.stdout), plain.stdout);
  assert.equal(strip(formatted.stderr), plain.stderr);
  assert.match(formatted.stdout, / INFO INFO mentions ERROR failed status=500/);
  assert.equal((formatted.stderr.match(/ ERROR /g) ?? []).length, 2); // prefix + original text
});

test('does not patch streams, process hooks or execArgv; leaves exit codes unchanged', () => {
  const result = run(`
    const { default: assert } = await import('node:assert/strict');
    assert.equal(process.listenerCount('uncaughtException'), 0);
    assert.equal(process.listenerCount('unhandledRejection'), 0);
    assert.ok(process.execArgv.includes(${JSON.stringify(preload.href)}));
    process.stdout.write('raw-json\\n'); process.stderr.write('raw-stderr\\n');
    console.error('caught startup failure'); process.exitCode = 1;
  `);
  assert.equal(result.status, 1);
  assert.equal(result.stdout, 'raw-json\n');
  assert.ok(result.stderr.startsWith('raw-stderr\n'));
  assert.match(result.stderr, / ERROR caught startup failure/);
  const crash = run(`throw new Error('uncaught failure');`);
  assert.equal(crash.status, 1);
  assert.match(crash.stderr, /uncaught failure/);
});

test('repeated module evaluation does not duplicate prefixes', () => {
  const result = run(`await import(${JSON.stringify(preload.href + '?again')}); console.log('once');`);
  assert.equal(result.status, 0);
  assert.equal(strip(result.stdout), 'once\n');
});

test('formatter failure falls back to the saved original method', () => {
  const result = run(`
    const { format } = await import('node:util');
    console.log = (...args) => process.stdout.write(format(...args) + '\\n');
    const NativeDate = Date;
    globalThis.Date = class { constructor() { throw new Error('clock unavailable'); } };
    await import(${JSON.stringify(preload.href)});
    console.log('fallback %s', 'works');
    globalThis.Date = NativeDate;
  `, false);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, 'fallback works\n');
});

test('real fork and Worker inherit preload without changing their output', () => {
  const dir = mkdtempSync(join(tmpdir(), 'clawweb-console-'));
  try {
    const child = join(dir, 'child.mjs');
    writeFileSync(child, `console.log('child-json'); console.error('child-error');`);
    // Run a file, not --input-type=module: fork inherits the service execArgv.
    const parent = join(dir, 'parent.mjs');
    writeFileSync(parent, `
      import { fork } from 'node:child_process';
      import { Worker } from 'node:worker_threads';
      import { once } from 'node:events';
      const child = fork(${JSON.stringify(child)}, [], { silent: true });
      child.stdout.pipe(process.stdout); child.stderr.pipe(process.stderr);
      const [code] = await once(child, 'exit');
      if (code !== 0) process.exitCode = 1;
      const worker = new Worker(new URL(${JSON.stringify(pathToFileURL(child).href)}), { stdout: true, stderr: true });
      worker.stdout.pipe(process.stdout); worker.stderr.pipe(process.stderr);
      const [workerCode] = await once(worker, 'exit');
      if (workerCode !== 0) process.exitCode = 1;
    `);
    const result = spawnSync(process.execPath, ['--import', preload.href, parent], { encoding: 'utf8', timeout: 15_000 });
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, 'child-json\nchild-json\n');
    assert.equal(result.stderr, 'child-error\nchild-error\n');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('delegating console helpers retain table/assert output and control behavior', () => {
  const source = `console.table([{ a: 1 }]); console.assert(true, 'not printed'); console.assert(false, 'assertion');`;
  const plain = run(source, false), formatted = run(source);
  assert.equal(formatted.status, plain.status);
  assert.equal(strip(formatted.stdout), plain.stdout);
  assert.equal(strip(formatted.stderr), plain.stderr);
});

test('import alone and public startup errors leave console unchanged', () => {
  const result = run(`const before = console.error; const {logStartupError} = await import(${JSON.stringify(moduleUrl)});
    if (console.error !== before) throw new Error('import changed console');
    logStartupError(false, 'public failure'); console.info('plain');`, false);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, 'public failure\n');
  assert.equal(result.stdout, 'plain\n');
});

test('early internal errors do not install globally or duplicate installed prefixes', () => {
  for (const enabled of [false, true]) {
    const result = run(`const {logStartupError} = await import(${JSON.stringify(moduleUrl)});
      logStartupError(true, 'startup failure'); console.info('after'); process.exitCode = 1;`, enabled);
    assert.equal(result.status, 1);
    assert.equal(strip(result.stderr), 'startup failure\n');
    assert.equal((result.stderr.match(/ ERROR /g) ?? []).length, 1);
    if (!enabled) assert.equal(result.stdout, 'after\n');
  }
});
