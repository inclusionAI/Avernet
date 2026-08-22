/**
 * Promise-chain serial queue — all git operations must go through this
 * to prevent concurrent working-tree corruption.
 *
 * Per-engine-instance (same bot = one engine instance).
 */
export class GitOperationQueue {
  private tail: Promise<void> = Promise.resolve();

  /** Enqueue an async operation. Returns the result of `fn`. */
  enqueue<T>(fn: () => Promise<T>): Promise<T> {
    const result = this.tail.then(() => fn());
    // Chain: the next operation waits for this one to settle (resolve or reject).
    // We swallow rejection so a failed operation doesn't block the queue.
    this.tail = result.then(
      () => {},
      () => {},
    );
    return result;
  }
}