/** A rejected conditional write is not a completed flow and is never retried as a transient error. */
export class CompletionRejectedError extends Error {
  readonly status = 409;
  constructor() { super('Flow completion was not admitted for the captured attempt'); }
}

/** Prevent the execution crash handler from turning a rejected closeout into another terminal write. */
export class CompletionPersistenceError extends Error {
  readonly status?: number;
  constructor(readonly cause: unknown) {
    super(`Flow completion persistence failed: ${cause instanceof Error ? cause.message : String(cause)}`);
    this.status = (cause as { status?: number })?.status;
  }
}

function transient(error: unknown): boolean {
  const status = (error as { status?: unknown })?.status;
  const code = typeof status === 'number' ? status : Number(/HTTP\s+(\d{3})/.exec(String(error))?.[1]);
  if (Number.isFinite(code) && code !== 0) return code === 408 || code === 425 || code === 429 || code >= 500;
  return true; // transport/DB errors have a finite retry budget too
}

export async function retryTerminalWrite<T>(
  operation: () => Promise<T>,
  options: { maxRetries?: number; baseDelayMs?: number } = {},
): Promise<T> {
  const { maxRetries = 2, baseDelayMs = 100 } = options;
  for (let attempt = 0; ; attempt++) {
    try { return await operation(); }
    catch (error) {
      if (!transient(error) || attempt >= maxRetries) throw error;
      await new Promise(resolve => setTimeout(resolve, baseDelayMs * 2 ** attempt));
    }
  }
}

/** Both reconciliation and publication must be downstream of an acknowledged conditional write. */
export async function persistBeforePublish(
  persist: () => Promise<boolean>, publish: () => Promise<void>,
  options?: { maxRetries?: number; baseDelayMs?: number },
): Promise<void> {
  try {
    await retryTerminalWrite(async () => {
      if (!await persist()) throw new CompletionRejectedError();
    }, options);
  } catch (error) {
    throw new CompletionPersistenceError(error);
  }
  await publish();
}
