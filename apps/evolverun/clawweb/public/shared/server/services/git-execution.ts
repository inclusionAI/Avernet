import { AsyncLocalStorage } from 'node:async_hooks';
import { execFile, type ExecFileOptions } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import { startWorkflowBackgroundRun, withWorkflowBackgroundContext, type WorkflowBackgroundFields } from './workflow-background-log.js';

// Shared by repair and AutoHeal in this process. Cross-instance task ownership
// remains the responsibility of the durable task repository.
const operations = new AsyncLocalStorage<{ deadline: number; active: boolean }>();
const waiting: Array<() => void> = [];
let active = 0;
const knownCommands = new Set(['init', 'remote', 'fetch', 'push', 'clone', 'checkout', 'config', 'add', 'commit',
  'rev-parse', 'ls-tree', 'show', 'ls-remote', 'rev-list', 'diff', 'update-ref', 'cat-file', 'log', 'hash-object', 'mktree', 'commit-tree', 'tag']);

function failure(code: string): Error { return Object.assign(new Error(code), { code }); }

async function acquire(deadline: number): Promise<() => void> {
  if (active < 2) active++;
  else {
    if (waiting.length >= 20) throw failure('GIT_QUEUE_FULL');
    await new Promise<void>((resolve, reject) => {
      const enter = () => { clearTimeout(timer); resolve(); };
      const timer = setTimeout(() => {
        const index = waiting.indexOf(enter);
        if (index >= 0) waiting.splice(index, 1);
        reject(failure('GIT_OPERATION_TIMEOUT'));
      }, Math.max(1, deadline - performance.now()));
      waiting.push(enter);
    });
  }
  return () => {
    const next = waiting.shift();
    if (next) next(); // Transfer the occupied slot without admitting a third job.
    else active--;
  };
}

/** Queue wait and all commands share one budget; cleanup finishes before slot release. */
export async function withGitOperation<T>(fields: WorkflowBackgroundFields, action: () => Promise<T>,
  options: { timeoutMs?: number } = {}): Promise<T> {
  const inherited = operations.getStore();
  if (inherited) {
    if (!inherited.active || performance.now() >= inherited.deadline) throw failure('GIT_OPERATION_TIMEOUT');
    return action();
  }
  const timeoutMs = options.timeoutMs ?? 120_000;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw failure('GIT_INVALID_TIMEOUT');
  return withWorkflowBackgroundContext(fields, async () => {
    const started = performance.now();
    const state = { deadline: started + timeoutMs, active: true };
    const end = startWorkflowBackgroundRun('workflow_git_operation', 'git_operation');
    let release: (() => void) | undefined;
    let queueWaitMs = 0;
    try {
      release = await acquire(state.deadline);
      queueWaitMs = Math.round(performance.now() - started);
      if (performance.now() >= state.deadline) throw failure('GIT_OPERATION_TIMEOUT');
      const result = await operations.run(state, action);
      end('succeeded', { queueWaitMs });
      return result;
    } catch (error) {
      const code = (error as { code?: string })?.code;
      if (!release) queueWaitMs = Math.round(performance.now() - started);
      end('failed', { queueWaitMs, errorCode: code === 'GIT_OPERATION_TIMEOUT' || code === 'GIT_QUEUE_FULL' ? code : 'GIT_OPERATION_FAILED' });
      throw error;
    } finally { state.active = false; release?.(); }
  });
}

/** Never retain argv, URLs, stderr or provider credentials in diagnostics/errors. */
export async function executeGit(args: string[], options: ExecFileOptions = {}): Promise<Buffer> {
  if (!operations.getStore()) return withGitOperation({}, () => executeGit(args, options));
  const state = operations.getStore()!;
  const remaining = Math.floor(state.deadline - performance.now());
  if (!state.active || remaining <= 0) throw failure('GIT_OPERATION_TIMEOUT');
  const timeout = Math.min(options.timeout && options.timeout > 0 ? options.timeout : 30_000, remaining);
  let index = 0;
  while (args[index] === '-c') index += 2;
  const op = knownCommands.has(args[index]) ? args[index] : 'other';
  const end = startWorkflowBackgroundRun('workflow_repair_git', 'git_command', { op });
  try {
    const stdout = await new Promise<Buffer>((resolve, reject) => {
      const child = execFile('git', args, { ...options, shell: false, timeout, killSignal: 'SIGKILL',
        maxBuffer: options.maxBuffer ?? 8 * 1024 * 1024, encoding: 'buffer' }, (error, out) => {
        if (!error) { resolve(out); return; }
        const code = error.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER' ? 'GIT_OUTPUT_LIMIT'
          : error.killed ? (timeout === remaining ? 'GIT_OPERATION_TIMEOUT' : 'GIT_TIMEOUT') : 'GIT_COMMAND_FAILED';
        reject(Object.assign(failure(code), { status: typeof error.code === 'number' ? error.code : undefined }));
      });
      child.stdin?.end();
    });
    end('succeeded');
    return stdout;
  } catch (error) {
    const source = error as { code?: unknown; status?: unknown };
    const code = source?.code === 'GIT_OPERATION_TIMEOUT' || source?.code === 'GIT_TIMEOUT' || source?.code === 'GIT_OUTPUT_LIMIT'
      ? source.code : 'GIT_COMMAND_FAILED';
    end('failed', { errorCode: code });
    // execFile may also throw synchronously; never expose its argv/error text.
    throw Object.assign(failure(code), { status: typeof source?.status === 'number' ? source.status : undefined });
  }
}
