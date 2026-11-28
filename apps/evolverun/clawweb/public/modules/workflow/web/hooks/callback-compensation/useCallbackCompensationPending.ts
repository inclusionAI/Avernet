import { callbackCompensationStorageKey } from '../../models/callbackCompensation';
import type { CallbackCompensationCreate } from '../../models/callbackCompensationTypes';
import type { CallbackCompensationContext } from '../../models/callbackCompensationTypes';
import { useRef, useState } from 'react';

// In-memory only: keep an ambiguous create frozen across route remounts. Never persist reasons/payloads/keys.
// Partitioning is for UI isolation, not authorization; every retry still goes through server auth.
const unresolvedCreates = new Map<string, CallbackCompensationCreate>();
function requestKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  return Array.from(
    bytes,
    (value, index) => `${[4, 6, 8, 10].includes(index) ? '-' : ''}${value.toString(16).padStart(2, '0')}`,
  ).join('');
}

/** The caller's keyed boundary remounts this hook when its actor or scope changes. */
export function useCallbackCompensationPending(
  context: Pick<CallbackCompensationContext, 'workflowId' | 'scopeKey' | 'userId'>,
  environment: string,
) {
  const key = callbackCompensationStorageKey({
    environment,
    workflowId: context.workflowId,
    scopeKey: context.scopeKey,
    userId: context.userId,
  });
  const [pending, setPending] = useState<CallbackCompensationCreate | null>(() => unresolvedCreates.get(key) ?? null);
  const pendingRef = useRef(pending);

  // Freeze before network I/O: a timeout/abort/error must never unlock a new preview or request key.
  const freezePending = (input: Omit<CallbackCompensationCreate, 'requestKey'>) => {
    if (pendingRef.current) return pendingRef.current;
    const value = { ...input, itemIds: [...input.itemIds], requestKey: requestKey() };
    unresolvedCreates.set(key, value);
    pendingRef.current = value;
    setPending(value);
    return value;
  };

  // Call only after a live, non-aborted create response has been accepted as the current batch.
  const resolvePending = () => {
    unresolvedCreates.delete(key);
    pendingRef.current = null;
    setPending(null);
  };

  return { pending, pendingRef, freezePending, resolvePending };
}
