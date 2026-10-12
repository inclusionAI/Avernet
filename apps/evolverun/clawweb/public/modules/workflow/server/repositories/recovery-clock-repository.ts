import { createHash } from "node:crypto";
import type { IDatabase } from '@avernet/clawweb-shared/server/db';

export type RecoveryClock = {
  ownerInstanceId: string;
  bootId: string;
  epoch: number;
  seq: number;
  elapsedMs: number;
  phase: 'recovering' | 'active' | 'retry_pending';
};
export type RecoveryClockCommand = RecoveryClock & {
  mode: 'claim' | 'checkpoint' | 'retry';
  expectedBootId?: string;
  requestId?: string;
};
export type ExpectedRecoveryClock = Pick<RecoveryClock, 'ownerInstanceId' | 'bootId' | 'epoch' | 'seq'>;
export class RecoveryClockConflict extends Error {
  constructor() { super('Recovery clock conflict'); }
}
export class RecoveryRunNotFound extends Error {
  constructor() { super('Flow run not found'); }
}

export function isExpectedRecoveryClock(value: unknown): value is ExpectedRecoveryClock {
  if (!value || typeof value !== 'object') return false;
  const v = value as ExpectedRecoveryClock;
  return validId(v.ownerInstanceId) && validId(v.bootId) && validCounter(v.epoch) && validCounter(v.seq);
}
function validId(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= 190;
}
function validCounter(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}
export function isRecoveryClockCommand(value: unknown): value is RecoveryClockCommand {
  if (!isExpectedRecoveryClock(value)) return false;
  const v = value as RecoveryClockCommand;
  return ['claim', 'checkpoint', 'retry'].includes(v.mode)
    && validCounter(v.elapsedMs) && ['recovering', 'active', 'retry_pending'].includes(v.phase)
    && (v.expectedBootId === undefined || validId(v.expectedBootId))
    && (v.requestId === undefined || validId(v.requestId))
    && (v.mode !== 'retry' || (validId(v.requestId) && v.phase === 'retry_pending' && v.elapsedMs === 0))
    && (v.mode !== 'claim' || v.phase !== 'active');
}

export class RecoveryClockRepository {
  constructor(private db: IDatabase) {}

  async get(flowId: string, db = this.db): Promise<RecoveryClock | null> {
    const rows = await db.query<RecoveryClock>(`SELECT owner_instance_id AS ownerInstanceId,
      boot_id AS bootId, timeout_epoch AS epoch, clock_seq AS seq, elapsed_ms AS elapsedMs, phase
      FROM flow_recovery_clocks WHERE flow_id = ?`, [flowId]);
    if (!rows[0]) return null;
    return { ...rows[0], epoch: Number(rows[0].epoch), seq: Number(rows[0].seq), elapsedMs: Number(rows[0].elapsedMs) };
  }

  /** All participating writes acquire the existing run row before reading the clock.
   * UPDATE obtains a DB row lock on MySQL/ZDAS and a write lock on SQLite.
   * This also serializes first claim against legacy timeout/reset operations. */
  async withRun<T>(flowId: string, fn: (tx: IDatabase, status: string | null) => Promise<T>): Promise<T> {
    return this.db.transaction(async tx => {
      await tx.exec('UPDATE flow_runs SET flow_id = flow_id WHERE flow_id = ?', [flowId]);
      const rows = await tx.query<{ status: string }>('SELECT status FROM flow_runs WHERE flow_id = ?', [flowId]);
      return fn(tx, rows[0]?.status ?? null);
    });
  }

  async put(flowId: string, input: RecoveryClockCommand): Promise<RecoveryClock> {
    if (!isRecoveryClockCommand(input)) throw new TypeError('Invalid recovery clock command');
    return this.withRun(flowId, async (tx, status) => {
      if (status === null) throw new RecoveryRunNotFound();
      const current = await this.get(flowId, tx);
      const next: RecoveryClock = { ownerInstanceId: input.ownerInstanceId, bootId: input.bootId,
        epoch: input.epoch, seq: input.seq, elapsedMs: input.elapsedMs, phase: input.phase };
      if (!current) {
        if (input.mode !== 'claim' || input.expectedBootId !== undefined || input.phase !== 'recovering') throw new RecoveryClockConflict();
        await tx.exec(`INSERT INTO flow_recovery_clocks
          (flow_id, owner_instance_id, boot_id, timeout_epoch, clock_seq, elapsed_ms, phase)
          VALUES (?, ?, ?, ?, ?, ?, ?)`, [flowId, next.ownerInstanceId, next.bootId, next.epoch, next.seq, next.elapsedMs, next.phase]);
        return next;
      }
      if (current.ownerInstanceId !== input.ownerInstanceId) throw new RecoveryClockConflict();
      if (input.mode === 'retry') {
        const payload = JSON.stringify(next);
        const key = createHash('sha256').update(JSON.stringify([flowId, input.requestId])).digest('hex');
        const prior = await tx.query<{ request_json: string }>(
          'SELECT request_json FROM flow_recovery_retries WHERE idempotency_key = ?', [key]);
        if (prior.length) {
          if (prior[0].request_json !== payload || current.epoch !== input.epoch) throw new RecoveryClockConflict();
          return current;
        }
        if (input.bootId !== current.bootId || input.epoch !== current.epoch + 1 || current.phase === 'retry_pending') throw new RecoveryClockConflict();
        await tx.exec('INSERT INTO flow_recovery_retries (flow_id, request_id, request_json, idempotency_key) VALUES (?, ?, ?, ?)',
          [flowId, input.requestId, payload, key]);
        await tx.exec('UPDATE flow_recovery_clocks SET retry_status = ? WHERE flow_id = ?', [status, flowId]);
      } else {
        if (input.epoch !== current.epoch || input.elapsedMs < current.elapsedMs || input.seq < current.seq) throw new RecoveryClockConflict();
        if (input.mode === 'claim' && input.bootId !== current.bootId) {
          if (input.expectedBootId !== current.bootId || input.seq <= current.seq
            || (current.phase === 'retry_pending' && input.phase !== 'retry_pending')
            || (current.phase !== 'retry_pending' && input.phase !== 'recovering')) throw new RecoveryClockConflict();
        } else {
          if (input.bootId !== current.bootId) throw new RecoveryClockConflict();
          if (input.seq === current.seq) {
            if (input.elapsedMs !== current.elapsedMs || input.phase !== current.phase) throw new RecoveryClockConflict();
            return current;
          }
          if (input.mode === 'claim' || (input.phase !== current.phase && input.phase !== 'active')) throw new RecoveryClockConflict();
        }
        if (input.mode === 'checkpoint' && input.phase === 'active' && current.phase === 'retry_pending') {
          const [prepared] = await tx.query<{ retry_status: string }>('SELECT retry_status FROM flow_recovery_clocks WHERE flow_id = ?', [flowId]);
          if (prepared?.retry_status !== status) throw new RecoveryClockConflict();
          await tx.exec("UPDATE flow_runs SET status = 'running', completed_at = NULL WHERE flow_id = ?", [flowId]);
        }
      }
      await tx.exec(`UPDATE flow_recovery_clocks SET boot_id = ?, timeout_epoch = ?, clock_seq = ?, elapsed_ms = ?, phase = ? WHERE flow_id = ?`,
        [next.bootId, next.epoch, next.seq, next.elapsedMs, next.phase, flowId]);
      return next;
    });
  }
}
