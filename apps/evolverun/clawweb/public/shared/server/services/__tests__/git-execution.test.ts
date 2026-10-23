import { afterEach, expect, it, vi } from 'vitest';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { executeGit, withGitOperation } from '../git-execution.js';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); });

it('bounds admitted operations, keeps FIFO order, and releases slots after failure', async () => {
  vi.spyOn(console, 'info').mockImplementation(() => {});
  const entered: number[] = [];
  const release: Array<() => void> = [];
  const jobs = Array.from({ length: 3 }, (_, i) => withGitOperation({ operation: 'test' }, async () => {
    entered.push(i);
    await new Promise<void>(resolve => { release[i] = resolve; });
    if (i === 0) throw new Error('expected');
  }).catch(error => error));
  await vi.waitFor(() => expect(entered).toEqual([0, 1]));
  release[0]();
  await vi.waitFor(() => expect(entered).toEqual([0, 1, 2]));
  release[1](); release[2]();
  await Promise.all(jobs);
});

it('expires queued work without executing it or leaking a slot', async () => {
  vi.spyOn(console, 'info').mockImplementation(() => {});
  const release: Array<() => void> = [];
  const held = [0, 1].map(i => withGitOperation({}, () => new Promise<void>(resolve => { release[i] = resolve; })));
  await vi.waitFor(() => expect(release.filter(Boolean)).toHaveLength(2));
  let ran = false;
  await expect(withGitOperation({}, async () => { ran = true; }, { timeoutMs: 30 })).rejects.toThrow('GIT_OPERATION_TIMEOUT');
  expect(ran).toBe(false);
  release.forEach(fn => fn()); await Promise.all(held);
  await expect(withGitOperation({}, async () => 'next')).resolves.toBe('next');
});

it('caps pending operations instead of retaining an unbounded queue', async () => {
  vi.spyOn(console, 'info').mockImplementation(() => {});
  const release: Array<() => void> = [];
  const held = [0, 1].map(i => withGitOperation({}, () => new Promise<void>(resolve => { release[i] = resolve; })));
  await vi.waitFor(() => expect(release.filter(Boolean)).toHaveLength(2));
  const queued = Array.from({ length: 20 }, () => withGitOperation({}, async () => {}));
  await expect(withGitOperation({}, async () => {})).rejects.toThrow('GIT_QUEUE_FULL');
  release.forEach(fn => fn()); await Promise.all([...held, ...queued]);
});

it('shares a deadline across commands and kills a stalled child before releasing the operation', async () => {
  const logs = vi.spyOn(console, 'info').mockImplementation(() => {});
  const root = await mkdtemp(path.join(tmpdir(), 'git-budget-'));
  await writeFile(path.join(root, 'git'), `#!${process.execPath}\nrequire('node:fs').writeFileSync('child.pid', String(process.pid));\nsetTimeout(() => process.stdout.write('ok'), Number(process.argv[2]));\n`, { mode: 0o700 });
  vi.stubEnv('PATH', root + path.delimiter + process.env.PATH);
  try {
    await expect(withGitOperation({ operation: 'budget', taskId: 'task-1' }, async () => {
      await executeGit(['350'], { cwd: root, timeout: 5000 });
      await executeGit(['10000', 'credential-must-not-be-logged'], { cwd: root, timeout: 5000 });
    }, { timeoutMs: 900 })).rejects.toThrow('GIT_OPERATION_TIMEOUT');
    const childPid = Number(await readFile(path.join(root, 'child.pid'), 'utf8'));
    expect(() => process.kill(childPid, 0)).toThrow();
    await expect(withGitOperation({}, () => executeGit(['1'], { cwd: root }))).resolves.toEqual(Buffer.from('ok'));
    const output = JSON.stringify(logs.mock.calls);
    expect(output).not.toContain('credential-must-not-be-logged');
    expect(output).toContain('GIT_OPERATION_TIMEOUT');
  } finally { await rm(root, { recursive: true, force: true }); }
});

it('nested operations inherit the original deadline instead of resetting the budget', async () => {
  vi.spyOn(console, 'info').mockImplementation(() => {});
  let ran = false;
  await expect(withGitOperation({}, async () => {
    await new Promise(resolve => setTimeout(resolve, 50));
    await withGitOperation({}, async () => { ran = true; }, { timeoutMs: 1000 });
  }, { timeoutMs: 20 })).rejects.toThrow('GIT_OPERATION_TIMEOUT');
  expect(ran).toBe(false);
});

it('reports bounded output separately from timeout and does not retain child output', async () => {
  const logs = vi.spyOn(console, 'info').mockImplementation(() => {});
  const root = await mkdtemp(path.join(tmpdir(), 'git-output-'));
  await writeFile(path.join(root, 'git'), `#!${process.execPath}\nprocess.stdout.write('test-output-sentinel'.repeat(100));\n`, { mode: 0o700 });
  vi.stubEnv('PATH', root + path.delimiter + process.env.PATH);
  try {
    await expect(executeGit(['show'], { cwd: root, maxBuffer: 64 })).rejects.toThrow('GIT_OUTPUT_LIMIT');
    expect(JSON.stringify(logs.mock.calls)).not.toContain('test-output-sentinel');
  } finally { await rm(root, { recursive: true, force: true }); }
});
