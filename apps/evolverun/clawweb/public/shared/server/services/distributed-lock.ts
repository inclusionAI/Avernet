import { DistributedLeaseLostError, DistributedLockRepository, type DistributedLockOptions } from '../repositories/distributed-lock-repository.js';
export { DistributedLeaseLostError };
export type { DistributedLockScope } from '../repositories/distributed-lock-repository.js';

export interface DistributedLease {
  /** Acquisition revision for correlation; renewal revisions remain internal. */
  readonly version: number;
  assertHeld(): void;
}
export interface WithLeaseOptions extends DistributedLockOptions {
  renewEveryMs: number;
}
export type WithLeaseResult<T> = { acquired: false } | { acquired: true; value: T };

/** Shared lease lifecycle; storage errors never enable an unlocked callback. */
export class DistributedLockService {
  constructor(private readonly repository: DistributedLockRepository) {}

  async withLease<T>(options: WithLeaseOptions, run: (lease: DistributedLease) => Promise<T>): Promise<WithLeaseResult<T>> {
    if (!Number.isSafeInteger(options.renewEveryMs) || options.renewEveryMs <= 0 || options.renewEveryMs >= options.leaseMs)
      throw new Error('Distributed lock renewal interval must be positive and shorter than its lease');
    const started = performance.now();
    let current = await this.repository.claim(options);
    if (!current) return { acquired: false };
    // Starting the local monotonic budget BEFORE I/O is conservative: slow
    // acquisition/renewal cannot extend ownership beyond the DB's deadline.
    let deadline = started + current.remainingMs;
    let lost: Error | undefined;
    let renewing: Promise<void> | undefined;
    const lease: DistributedLease = {
      version: current.version,
      assertHeld() {
        if (!lost && performance.now() >= deadline) lost = new DistributedLeaseLostError();
        if (lost) throw lost;
      },
    };
    const timer = setInterval(() => {
      if (renewing || lost) return;
      try { lease.assertHeld(); } catch { return; }
      const renewalStarted = performance.now();
      renewing = this.repository.renew(current!).then(next => {
        // Retain every successful DB revision for cleanup, even after local
        // ownership was lost. A late receipt must never revive business work.
        current = next;
        lease.assertHeld();
        deadline = renewalStarted + next.remainingMs;
        lease.assertHeld();
      }).catch(error => {
        lost = error instanceof Error ? error : new DistributedLeaseLostError();
      }).finally(() => { renewing = undefined; });
    }, options.renewEveryMs).unref();
    let failed = false;
    let failure: unknown;
    let value!: T;
    try {
      lease.assertHeld();
      value = await run(lease);
      lease.assertHeld();
    } catch (error) { failed = true; failure = error; }
    clearInterval(timer);
    await renewing;
    if (!failed) {
      try { lease.assertHeld(); } catch (error) { failed = true; failure = error; }
    }
    try {
      const released = await this.repository.release(current);
      if (!released && !failed) { failed = true; failure = new DistributedLeaseLostError(); }
    } catch (error) {
      failure = failed ? new AggregateError([failure, error], 'Distributed lock work and release failed') : error;
      failed = true;
    }
    if (failed) throw failure;
    return { acquired: true, value };
  }
}
