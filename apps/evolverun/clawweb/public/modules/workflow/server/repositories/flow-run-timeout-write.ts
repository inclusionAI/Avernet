import type { IDatabase } from '@avernet/clawweb-shared/server/db';

import { writeRunSettlement, type CompletionOutcome } from './flow-run-write-fence.js';
export type { CompletionOutcome } from './flow-run-write-fence.js';
type JsonObject = Record<string, unknown>;
type TimeoutFields = {
  resultJson: unknown;
  retryEpoch?: number;
  totalDurationMs?: number | null;
};

function objectJson(value: unknown): JsonObject | null {
  if (typeof value !== 'string') return null;
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as JsonObject : null;
  } catch { return null; }
}

/** The controller wraps the original watchdog JSON in buildFailedResultJson.message. */
export function isTimeoutResult(value: unknown): boolean {
  let object = objectJson(value);
  for (let depth = 0; object && depth < 4; depth++) {
    if (object.reason === 'flow_timeout' || object.reason === 'timeout') return true;
    object = objectJson(object.message);
  }
  return false;
}

/** A watchdog claim is strictly running; the executor alone decides expiry. */
export async function writeTimeoutFailure(
  db: IDatabase,
  flowId: string,
  fields: TimeoutFields,
  patch: (serverCompletedAt: number) => { sets: string[]; values: unknown[] },
): Promise<CompletionOutcome> {
  if (typeof fields.resultJson !== 'string' || !fields.resultJson.trim()) return 'rejected';
  return writeRunSettlement(db, flowId, { ...fields, status: 'failed', resultJson: fields.resultJson },
    () => patch(Math.floor(Date.now() / 1000)), false, true);
}
