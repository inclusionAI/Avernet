/** Omitted epochs authorize only the historical attempt (epoch 0).
 * Never infer an expected epoch from a current database row during closeout. */
export function expectedRetryEpoch(value: unknown): number {
  if (value === undefined) return 0;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new TypeError("retry epoch must be a non-negative safe integer");
  }
  return value;
}

export type StateAttempt = { retryEpoch?: number; recoveryEpoch?: number };

/** Original checkpoint fields only. A present invalid alias never falls back. */
export function stateAttemptEpoch(state: unknown): number {
  if (!state || typeof state !== 'object' || Array.isArray(state)) throw new TypeError('Invalid execution state object');
  const aliases = ['retryEpoch', 'recoveryEpoch'].filter(key => Object.hasOwn(state, key));
  let epoch = 0;
  for (const [index, key] of aliases.entries()) {
    const value = (state as Record<string, unknown>)[key];
    if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
      throw new TypeError('Invalid state retry/recovery epoch');
    }
    if (index && value !== epoch) throw new TypeError('State retry/recovery epoch mismatch');
    epoch = value;
  }
  return epoch;
}

/** TaskGuard writes its canonical field; preserve imported alias consistency. */
export function nextAttemptState<T extends StateAttempt>(state: T, nextEpoch: number): T & { retryEpoch: number } {
  stateAttemptEpoch(state);
  expectedRetryEpoch(nextEpoch);
  return { ...state, retryEpoch: nextEpoch,
    ...(Object.hasOwn(state, 'recoveryEpoch') ? { recoveryEpoch: nextEpoch } : {}) };
}
