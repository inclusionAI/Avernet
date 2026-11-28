/** Shared started_at is immutable run lineage. Explicit retries carry their own
 * wall-clock window in the atomically admitted checkpoint instead. */
export function flowTimeoutElapsedMs(
  row: { started_at?: number | null }, state?: { retryStartedAtMs?: unknown }, now = Date.now(),
): number {
  const anchor = state && Object.hasOwn(state, 'retryStartedAtMs') ? state.retryStartedAtMs : undefined;
  if (state && Object.hasOwn(state, 'retryStartedAtMs')) {
    if (typeof anchor !== 'number' || !Number.isSafeInteger(anchor) || anchor <= 0 || anchor > now) {
      throw new Error('Invalid retry timeout clock anchor; timeout claim deferred');
    }
    return now - anchor;
  }
  if (!Number.isFinite(row.started_at) || row.started_at <= 0) throw new Error('Invalid flow start time');
  return Math.max(0, now - row.started_at * 1000);
}
