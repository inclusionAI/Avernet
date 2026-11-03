import type { IDatabase } from '@avernet/clawweb-shared/server/db';

export type PackOperationPhase = 'preparing' | 'prepared' | 'pushing' | 'unknown' | 'completed' | 'failed';
export type BeginPackOperationInput = {
  requestId: string;
  workflowId: string;
  actorId: string;
  kind: 'save' | 'deploy';
  inputHash: string;
};
export type PackOperationBaseline = {
  workflow: { workflow_id: string; id: number; spec_json: string; pack_id: string | null; version: number | null; owner_id: string | null };
  active: Array<{
    id: number; workflow_id: string; pack_id: string; deploy_number: number; version: number;
    tag_name: string | null; action: string; from_deploy_number: number | null;
    spec_json: string; note: string | null; bot_id: string | null; owner_id: string | null; is_active: number;
  }>;
};
export type PackOperation = BeginPackOperationInput & {
  phase: PackOperationPhase;
  attempt: number;
  baseline: PackOperationBaseline;
  payloadJson: string | null;
  resultJson: string | null;
  errorJson: string | null;
  createdAtMs: number;
  updatedAtMs: number;
};
export class PackOperationError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
    this.name = 'PackOperationError';
  }
}

type OperationRow = {
  request_id: string; workflow_id: string; actor_id: string; kind: 'save' | 'deploy'; input_hash: string;
  phase: PackOperationPhase; attempt: number; lock_workflow_id: number | null; baseline_json: string;
  payload_json: string | null; result_json: string | null; error_json: string | null;
  created_at_ms: number; updated_at_ms: number;
};
const workflowFields = 'workflow_id, id, spec_json, pack_id, version, owner_id';
const activeFields = 'id, workflow_id, pack_id, deploy_number, version, tag_name, action, from_deploy_number, spec_json, note, bot_id, owner_id, is_active';
const lockClause = (db: IDatabase) => db.dbType === 'mysql' || db.dbType === 'zdas' ? ' FOR UPDATE' : '';
const conflict = (code: string, message: string) => new PackOperationError(409, code, message);
function decode(row: OperationRow): PackOperation {
  return {
    requestId: row.request_id, workflowId: row.workflow_id, actorId: row.actor_id, kind: row.kind,
    inputHash: row.input_hash, phase: row.phase, attempt: Number(row.attempt), baseline: JSON.parse(row.baseline_json),
    payloadJson: row.payload_json, resultJson: row.result_json, errorJson: row.error_json,
    createdAtMs: Number(row.created_at_ms), updatedAtMs: Number(row.updated_at_ms),
  };
}
function requireJson(value: string): void {
  try { if (typeof value !== 'string') throw new Error('not a string'); JSON.parse(value); }
  catch { throw new PackOperationError(400, 'INVALID_JSON', 'Expected a JSON string'); }
}
function sameIdentity(operation: PackOperation, input: BeginPackOperationInput): void {
  if (operation.requestId !== input.requestId || operation.workflowId !== input.workflowId
    || operation.actorId !== input.actorId || operation.kind !== input.kind || operation.inputHash !== input.inputHash) {
    throw conflict('REQUEST_CONFLICT', 'Request ID is already associated with different input');
  }
}
function isUniqueViolation(error: unknown): boolean {
  const code = (error as { code?: string })?.code;
  return code === 'ER_DUP_ENTRY' || code === 'SQLITE_CONSTRAINT_UNIQUE' || code === 'SQLITE_CONSTRAINT_PRIMARYKEY';
}

/** ClawWeb's journal only; no history writes, Git calls, TTLs or process-local locks.
 * Callbacks must use the supplied transaction and must not perform network I/O.
 * Route/service authorization must check actorId/workflowId before resuming.
 * Only a successful markPushing claim authorizes the caller to start its push.
 */
export class PackOperationRepository {
  constructor(private readonly db: IDatabase) {}

  async get(requestId: string): Promise<PackOperation | null> {
    return this.read(this.db, requestId);
  }

  /** Exact saved-candidate provenance; a recreated workflow cannot inherit the
   * previous row's save records. Parsing JSON (not LIKE) is the final authority.
   */
  async findSavedCandidate(workflowId: string, commit: string): Promise<boolean> {
    return this.db.transaction(async tx => {
      const workflow = await this.lockWorkflow(tx, workflowId);
      if (!workflow) return false;
      const rows = await tx.query<Pick<OperationRow, 'baseline_json' | 'payload_json'>>(
        "SELECT baseline_json, payload_json FROM clawweb_pack_operations WHERE workflow_id = ? AND kind = 'save' AND phase = 'completed'",
        [workflowId]);
      return rows.some(row => {
        const baseline = JSON.parse(row.baseline_json) as PackOperationBaseline;
        if (baseline.workflow.id !== workflow.id || row.payload_json === null) return false;
        const payload = JSON.parse(row.payload_json) as { git?: { commit?: unknown } } | null;
        return typeof payload?.git?.commit === 'string' && payload.git.commit === commit;
      });
    });
  }

  async begin(input: BeginPackOperationInput): Promise<{ operation: PackOperation; created: boolean }> {
    for (const [name, limit] of [['requestId', 190], ['workflowId', 255], ['actorId', 255], ['inputHash', 128]] as const) {
      if (typeof input[name] !== 'string' || !input[name].trim() || input[name].length > limit) {
        throw new PackOperationError(400, 'INVALID_INPUT', `${name} must contain 1-${limit} characters`);
      }
    }
    if (input.kind !== 'save' && input.kind !== 'deploy') throw new PackOperationError(400, 'INVALID_INPUT', 'Unsupported operation kind');
    try {
      return await this.db.transaction(async tx => {
        // First transactional read is a current/locking read of the REAL workflow.
        // SQLite's adapter begins IMMEDIATE; MySQL/ZDAS lock this row across hosts.
        const workflow = await this.lockWorkflow(tx, input.workflowId);
        const existing = await this.read(tx, input.requestId);
        if (existing) return this.replay(input, existing, workflow);
        if (!workflow) throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND', 'Workflow not found');
        const busy = await tx.query('SELECT request_id FROM clawweb_pack_operations WHERE workflow_id = ? AND lock_workflow_id IS NOT NULL', [input.workflowId]);
        if (busy.length) throw conflict('WORKFLOW_BUSY', 'An unfinished pack operation holds this workflow');
        const baseline = { workflow, active: await this.readActive(tx, input.workflowId) };
        const now = Date.now();
        const inserted = await tx.exec(`INSERT INTO clawweb_pack_operations
          (request_id, workflow_id, actor_id, kind, input_hash, phase, attempt, lock_workflow_id, baseline_json,
           payload_json, result_json, error_json, created_at_ms, updated_at_ms)
          VALUES (?, ?, ?, ?, ?, 'preparing', 1, ?, ?, NULL, NULL, NULL, ?, ?)`,
        [input.requestId, input.workflowId, input.actorId, input.kind, input.inputHash, workflow.id, JSON.stringify(baseline), now, now]);
        if (inserted.affectedRows !== 1) throw new Error('Pack operation insert did not persist exactly one row');
        return { created: true, operation: { ...input, phase: 'preparing' as const, attempt: 1, baseline,
          payloadJson: null, resultJson: null, errorJson: null, createdAtMs: now, updatedAtMs: now } };
      });
    } catch (error) {
      // Different workflows can concurrently contend for the globally unique
      // request ID. Classify only duplicate-key errors after the tx rolled back.
      if (!isUniqueViolation(error)) throw error;
      return this.db.transaction(async tx => {
        const workflow = await this.lockWorkflow(tx, input.workflowId);
        const winner = await this.read(tx, input.requestId, true);
        if (winner) return this.replay(input, winner, workflow);
        throw conflict('WORKFLOW_BUSY', 'A request or workflow operation lock already exists');
      });
    }
  }

  /** Recovery must validate before touching Git, including pushing/unknown.
   * This is a preflight, not a new push claim or an authorization check.
   */
  async preflightRecovery(requestId: string, expectedAttempt: number): Promise<PackOperation> {
    return this.mutate(requestId, expectedAttempt, async (tx, operation, workflow) => {
      if (operation.phase === 'completed') return operation;
      if (!['prepared', 'pushing', 'unknown'].includes(operation.phase)) {
        throw conflict('PHASE_CONFLICT', 'Only prepared or started operations can recover');
      }
      await this.checkBaseline(tx, operation, workflow);
      return operation;
    });
  }

  /** Retry only a proven pre-push failure, under its original immutable input
   * and baseline. Concurrent retry losers get 409; no uncertain push is reset.
   */
  async restartFailed(input: BeginPackOperationInput, expectedAttempt: number): Promise<{ operation: PackOperation; created: boolean }> {
    return this.mutate(input.requestId, expectedAttempt, async (tx, operation, workflow) => {
      sameIdentity(operation, input);
      if (operation.phase !== 'failed') throw conflict('PHASE_CONFLICT', 'Only a confirmed pre-push failure can restart');
      await this.checkBaseline(tx, operation, workflow);
      const busy = await tx.query('SELECT request_id FROM clawweb_pack_operations WHERE workflow_id = ? AND lock_workflow_id IS NOT NULL', [input.workflowId]);
      if (busy.length) throw conflict('WORKFLOW_BUSY', 'Another unfinished pack operation holds this workflow');
      if (!Number.isSafeInteger(operation.attempt + 1)) throw conflict('ATTEMPT_STALE', 'Attempt generation exhausted');
      return { created: true, operation: await this.write(tx, operation, {
        attempt: operation.attempt + 1, phase: 'preparing', payloadJson: null, resultJson: null, errorJson: null,
      }) };
    });
  }

  async prepare(requestId: string, payloadJson: string, expectedAttempt: number): Promise<PackOperation> {
    requireJson(payloadJson);
    return this.mutate(requestId, expectedAttempt, async (tx, operation, workflow) => {
      if (operation.payloadJson !== null) {
        if (operation.payloadJson !== payloadJson) throw conflict('PAYLOAD_CONFLICT', 'Prepared payload is immutable');
        return operation;
      }
      if (operation.phase !== 'preparing') throw conflict('PHASE_CONFLICT', 'Only preparing operations can be prepared');
      await this.checkBaseline(tx, operation, workflow);
      return this.write(tx, operation, { phase: 'prepared', payloadJson });
    });
  }

  async markPushing(requestId: string, expectedAttempt: number): Promise<{ operation: PackOperation; claimed: boolean }> {
    return this.mutate(requestId, expectedAttempt, async (tx, operation, workflow) => {
      if (operation.phase === 'preparing') throw conflict('PHASE_CONFLICT', 'Prepare an immutable payload before pushing');
      if (operation.phase !== 'prepared') return { operation, claimed: false };
      await this.checkBaseline(tx, operation, workflow);
      return { operation: await this.write(tx, operation, { phase: 'pushing' }), claimed: true };
    });
  }

  async complete(requestId: string, resultJson: string,
    businessCallback: (tx: IDatabase, operation: PackOperation) => Promise<void>, expectedAttempt: number): Promise<PackOperation> {
    requireJson(resultJson);
    return this.mutate(requestId, expectedAttempt, async (tx, operation, workflow) => {
      if (operation.phase === 'completed') {
        if (operation.resultJson !== resultJson) throw conflict('RESULT_CONFLICT', 'Completed result is immutable');
        return operation;
      }
      if (operation.phase !== 'pushing' && operation.phase !== 'unknown') {
        throw conflict('PHASE_CONFLICT', 'Only a pushed or reconciled unknown operation can complete');
      }
      await this.checkBaseline(tx, operation, workflow);
      // Give the caller its own snapshot, so accidental mutation cannot redirect
      // the journal update or release another operation's lock.
      await businessCallback(tx, structuredClone(operation));
      return this.write(tx, operation, { phase: 'completed', resultJson, errorJson: null });
    });
  }

  async failBeforePush(requestId: string, errorJson: string, expectedAttempt: number): Promise<PackOperation> {
    requireJson(errorJson);
    return this.mutate(requestId, expectedAttempt, async (tx, operation) => {
      if (operation.phase === 'failed') {
        if (operation.errorJson !== errorJson) throw conflict('RESULT_CONFLICT', 'Failure result is immutable');
        return operation;
      }
      if (operation.phase !== 'preparing' && operation.phase !== 'prepared') {
        throw conflict('PHASE_CONFLICT', 'A started or uncertain push cannot release the workflow lock');
      }
      return this.write(tx, operation, { phase: 'failed', errorJson });
    });
  }

  async markUnknown(requestId: string, errorJson: string, expectedAttempt: number): Promise<PackOperation> {
    requireJson(errorJson);
    return this.mutate(requestId, expectedAttempt, async (tx, operation) => {
      if (operation.phase !== 'pushing' && operation.phase !== 'unknown') {
        throw conflict('PHASE_CONFLICT', 'Only a started push may have an unknown outcome');
      }
      if (operation.phase === 'unknown' && operation.errorJson === errorJson) return operation;
      return this.write(tx, operation, { phase: 'unknown', errorJson });
    });
  }

  private replay(input: BeginPackOperationInput, operation: PackOperation,
    workflow: PackOperationBaseline['workflow'] | null): { operation: PackOperation; created: boolean } {
    sameIdentity(operation, input);
    // A successful callback legitimately changed spec/version. Only the row's
    // identity must still match; deletion/recreation is NOT a successful retry.
    if (!workflow || workflow.id !== operation.baseline.workflow.id) {
      throw conflict('BASELINE_STALE', 'The original workflow instance no longer exists');
    }
    return { operation, created: false };
  }

  private async read(db: IDatabase, requestId: string, lock = false): Promise<PackOperation | null> {
    const rows = await db.query<OperationRow>(`SELECT * FROM clawweb_pack_operations WHERE request_id = ?${lock ? lockClause(db) : ''}`, [requestId]);
    // MySQL installations may have a case-insensitive default collation.
    if (!rows[0] || rows[0].request_id !== requestId) return null;
    return decode(rows[0]);
  }

  private async lockWorkflow(tx: IDatabase, workflowId: string): Promise<PackOperationBaseline['workflow'] | null> {
    const rows = await tx.query<PackOperationBaseline['workflow']>(
      `SELECT ${workflowFields} FROM workflow_specs WHERE workflow_id = ?${lockClause(tx)}`, [workflowId]);
    if (rows[0] && rows[0].workflow_id !== workflowId) {
      throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND', 'Workflow ID must match exactly');
    }
    return rows[0] ?? null;
  }

  private readActive(tx: IDatabase, workflowId: string): Promise<PackOperationBaseline['active']> {
    return tx.query(`SELECT ${activeFields} FROM workflow_deploy_history
      WHERE workflow_id = ? AND is_active = 1 ORDER BY id${lockClause(tx)}`, [workflowId]);
  }

  private async checkBaseline(tx: IDatabase, operation: PackOperation,
    workflow: PackOperationBaseline['workflow'] | null): Promise<void> {
    if (!workflow || JSON.stringify(workflow) !== JSON.stringify(operation.baseline.workflow)
      || JSON.stringify(await this.readActive(tx, operation.workflowId)) !== JSON.stringify(operation.baseline.active)) {
      throw conflict('BASELINE_STALE', 'Workflow or active deployment changed; no business writes were applied');
    }
  }

  private async mutate<T>(requestId: string, expectedAttempt: number, fn: (tx: IDatabase, operation: PackOperation,
    workflow: PackOperationBaseline['workflow'] | null) => Promise<T>): Promise<T> {
    if (!Number.isSafeInteger(expectedAttempt) || expectedAttempt < 1) {
      throw conflict('ATTEMPT_STALE', 'Invalid attempt generation');
    }
    // Lookup only routes to the lock; never trust its phase for a state change.
    const hint = await this.get(requestId);
    if (!hint) throw new PackOperationError(404, 'OPERATION_NOT_FOUND', 'Pack operation not found');
    return this.db.transaction(async tx => {
      const workflow = await this.lockWorkflow(tx, hint.workflowId);
      const current = await this.read(tx, requestId, true);
      if (!current) throw new PackOperationError(404, 'OPERATION_NOT_FOUND', 'Pack operation not found');
      if (current.attempt !== expectedAttempt) throw conflict('ATTEMPT_STALE', 'Attempt generation is stale');
      return fn(tx, current, workflow);
    });
  }

  private async write(tx: IDatabase, current: PackOperation,
    change: Partial<Pick<PackOperation, 'attempt' | 'phase' | 'payloadJson' | 'resultJson' | 'errorJson'>>): Promise<PackOperation> {
    const next = { ...current, ...change, updatedAtMs: Date.now() };
    const terminal = next.phase === 'completed' || next.phase === 'failed';
    const updated = await tx.exec(`UPDATE clawweb_pack_operations SET phase = ?, attempt = ?, payload_json = ?, result_json = ?,
      error_json = ?, lock_workflow_id = ?, updated_at_ms = ? WHERE request_id = ? AND phase = ? AND attempt = ?`,
    [next.phase, next.attempt, next.payloadJson, next.resultJson, next.errorJson, terminal ? null : next.baseline.workflow.id,
      next.updatedAtMs, current.requestId, current.phase, current.attempt]);
    if (updated.affectedRows !== 1) throw new Error('Pack operation update did not persist exactly one row');
    return next;
  }
}
