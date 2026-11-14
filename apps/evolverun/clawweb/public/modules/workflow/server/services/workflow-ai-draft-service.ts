import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { WorkflowAiDraftRepository, type AiDraftRow } from '../repositories/workflow-ai-draft-repository.js';
import { WorkflowAiDraftDispatchRejectedError, WorkflowAiDraftError, WorkflowAiDraftInvalidResultError,
  type WorkflowAiDraftDetail, type WorkflowAiDraftEnvelope, type WorkflowAiDraftExecutionPort,
  type WorkflowAiDraftService, type WorkflowAiDraftTarget } from '../contracts/workflow-ai-draft.js';
import { aiDraftSha256, aiDraftUuid, aiDraftValidText, assertAiDraftWorkflowId, validateAiDraftRequest, validateAiDraftOutput } from './workflow-ai-draft-validation.js';

const terminal = (row: AiDraftRow) => ['completed', 'failed', 'cancelled'].includes(row.status);
const MAX_CAS_ATTEMPTS = 8;
const POLL_MS = 15_000;
const MAX_EXECUTION_MS = 30 * 60_000;
const RECONCILE_CONCURRENCY = 4;
/** Library service: all mutations are isolated task records. A durable uncertain
 * fence precedes external dispatch; GET/timer poll known jobs, never resubmit. */
export function createWorkflowAiDraftService(input: {
  db: IDatabase; canEdit(workflowId: string, actorId: string): Promise<boolean>;
  execution?: WorkflowAiDraftExecutionPort; unavailableReason?: string; now?: () => number;
}): WorkflowAiDraftService {
  const repo = new WorkflowAiDraftRepository(input.db);
  const now = input.now ?? Date.now;
  const port = input.execution;
  async function access(actorId: string, workflowId: string) {
    assertAiDraftWorkflowId(workflowId);
    if (typeof actorId !== 'string' || !actorId.trim() || actorId.trim() !== actorId || actorId.length > 128
      || actorId === '*' || actorId === 'unknown' || /[\x00-\x1f\x7f]/.test(actorId)
      || !await input.canEdit(workflowId, actorId)) throw new WorkflowAiDraftError('FORBIDDEN');
  }
  async function owned(actorId: string, workflowId: string, draftId: string) {
    await access(actorId, workflowId);
    if (!aiDraftUuid(draftId)) throw new WorkflowAiDraftError('INVALID_INPUT');
    const row = await repo.get(draftId);
    if (!row || row.actor_id !== actorId || row.workflow_id !== workflowId) throw new WorkflowAiDraftError('NOT_FOUND');
    return row;
  }
  async function resolveTarget(): Promise<WorkflowAiDraftTarget> {
    try {
      const target = await port!.resolveTarget();
      if (typeof target?.botId !== 'string' || !target.botId.trim() || target.botId.trim() !== target.botId
        || !aiDraftValidText(target.botId) || /[\x00-\x1f\x7f]/.test(target.botId)) throw new Error('invalid target');
      // Preserve the full host-owned Bot identity, including its owner suffix.
      return { botId: target.botId };
    } catch { throw new WorkflowAiDraftError('CAPABILITY_UNAVAILABLE', 'AUTO_HEAL_BOT_UNAVAILABLE'); }
  }
  const envelope = (row: AiDraftRow) => JSON.parse(row.envelope_json) as WorkflowAiDraftEnvelope;
  const detail = (row: AiDraftRow): WorkflowAiDraftDetail => ({ draftId: row.draft_id, workflowId: row.workflow_id, status: row.status,
    ...(row.error_code ? { error: row.error_code } : {}),
    ...(row.status === 'completed' && row.result_json ? { result: JSON.parse(row.result_json) } : {}),
    inputSpecDigest: envelope(row).input.workingCopy.sha256 });
  async function reload(row: AiDraftRow) {
    const actual = await repo.get(row.draft_id);
    if (!actual) throw new Error('AI_DRAFT_STORAGE_ERROR');
    return actual;
  }
  async function finish(row: AiDraftRow, status: 'failed' | 'completed' | 'cancelled', error: string | null,
    result: { yaml: string; summary: string } | null = null) {
    if (!terminal(row)) await repo.update(row, { status, error_code: error, result_json: result ? JSON.stringify(result) : null }, now());
    return reload(row);
  }
  async function dispatch(row: AiDraftRow): Promise<AiDraftRow> {
    if (!port || row.status !== 'pending') return row;
    if (now() - row.created_at_ms >= MAX_EXECUTION_MS) return finish(row, 'failed', 'EXECUTION_TIMEOUT');
    if (!await input.canEdit(row.workflow_id, row.actor_id)) return finish(row, 'failed', 'EDIT_PERMISSION_REVOKED');
    if (!await repo.update(row, { status: 'dispatch_uncertain', next_poll_at_ms: now() + POLL_MS }, now())) return reload(row);
    row = await reload(row);
    if (terminal(row)) return row;
    if (now() - row.created_at_ms >= MAX_EXECUTION_MS) return finish(row, 'failed', 'EXECUTION_TIMEOUT');
    if (!await input.canEdit(row.workflow_id, row.actor_id)) return finish(row, 'failed', 'EDIT_PERMISSION_REVOKED');
    let jobId: string;
    try {
      ({ jobId } = await port.dispatch({ actorId: row.actor_id, envelope: envelope(row) }));
      if (typeof jobId !== 'string' || !jobId.trim() || jobId.length > 255 || !aiDraftValidText(jobId)
        || /[\x00-\x1f\x7f]/.test(jobId)) throw new Error('invalid job');
    } catch (error) {
      if (error instanceof WorkflowAiDraftDispatchRejectedError) return finish(await reload(row), 'failed', error.code);
      // Submission may have been accepted. Without an acknowledged job ID there
      // is nothing to poll; leave the durable fence until cancellation/timeout.
      return reload(row);
    }
    for (let attempt = 0; attempt < MAX_CAS_ATTEMPTS; attempt++) {
      const current = await reload(row);
      // Cancellation is local; neither late acknowledgements nor results reopen it.
      if (terminal(current) || current.job_id) return current;
      if (await repo.update(current, { job_id: jobId, status: 'running' }, now())) return reload(row);
    }
    // Contention cannot hold a request forever or reopen dispatch. Its durable
    // uncertain fence remains authoritative even if the acknowledgement is lost.
    throw new WorkflowAiDraftError('STATE_CONFLICT');
  }
  async function reconcileRow(row: AiDraftRow): Promise<AiDraftRow> {
    if (terminal(row) || !port || row.next_poll_at_ms > now()) return row;
    if (row.status === 'pending') return dispatch(row);
    if (!await input.canEdit(row.workflow_id, row.actor_id)) return finish(row, 'failed', 'EDIT_PERMISSION_REVOKED');
    // Admission advances only the due clock, not the business CAS version. A
    // slower valid reply must survive another poll claim, but never a cancel.
    if (!await repo.claimDue(row, now(), now() + POLL_MS)) return reload(row);
    row = await reload(row);
    if (terminal(row)) return row;
    if (now() - row.created_at_ms >= MAX_EXECUTION_MS) {
      return finish(row, 'failed', row.job_id ? 'EXECUTION_TIMEOUT' : 'DISPATCH_UNCERTAIN_TIMEOUT');
    }
    if (!row.job_id) return row;
    let generated: Awaited<ReturnType<WorkflowAiDraftExecutionPort['poll']>>;
    try { generated = await port.poll({ actorId: row.actor_id, envelope: envelope(row), jobId: row.job_id }); }
    catch (error) {
      if (error instanceof WorkflowAiDraftInvalidResultError) return finish(row, 'failed', 'INVALID_GENERATION_RESULT');
      // A transport failure is not terminal evidence; retry within the deadline.
      return reload(row);
    }
    // Provider work can outlive an edit grant. Recheck before accepting any
    // result; a concurrent cancel still wins through the existing CAS fence.
    if (!await input.canEdit(row.workflow_id, row.actor_id)) return finish(row, 'failed', 'EDIT_PERMISSION_REVOKED');
    if (now() - row.created_at_ms >= MAX_EXECUTION_MS) return finish(row, 'failed', 'EXECUTION_TIMEOUT');
    if (generated?.status === 'running') return reload(row);
    if (generated?.status === 'failed') {
      const code = typeof generated.error === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(generated.error) ? generated.error : 'GENERATION_FAILED';
      return finish(row, 'failed', code);
    }
    if (generated?.status !== 'completed' || !generated.result) return finish(row, 'failed', 'INVALID_GENERATION_RESULT');
    const result = generated.result;
    try {
      validateAiDraftOutput(result.yaml, envelope(row).input.workingCopy.yaml, row.workflow_id);
      if (typeof result.summary !== 'string' || !aiDraftValidText(result.summary) || result.summary.includes('\0')
        || result.summary.length > 4000) throw new Error('invalid summary');
    } catch { return finish(row, 'failed', 'INVALID_GENERATION_RESULT'); }
    return finish(row, 'completed', null, { yaml: result.yaml, summary: result.summary });
  }
  return {
    async capabilities(actorId, workflowId) {
      await access(actorId, workflowId);
      if (input.db.dbType === 'noop') return { available: false, reason: 'AI_DRAFT_STORAGE_UNAVAILABLE' };
      if (!port) return { available: false, reason: input.unavailableReason ?? 'AI_DRAFT_PROVIDER_UNAVAILABLE' };
      try { await resolveTarget(); return { available: true }; }
      catch { return { available: false, reason: 'AUTO_HEAL_BOT_UNAVAILABLE' }; }
    },
    async create(actorId, workflowId, request) {
      await access(actorId, workflowId);
      validateAiDraftRequest(workflowId, request);
      const requestKey = aiDraftSha256(JSON.stringify([workflowId, actorId, request.requestId.toLowerCase()]));
      const digest = aiDraftSha256(JSON.stringify([request.mode, request.prompt, request.currentYaml]));
      const checked = async (row: AiDraftRow) => {
        if (row.workflow_id !== workflowId || row.actor_id !== actorId || row.request_digest !== digest) throw new WorkflowAiDraftError('IDEMPOTENCY_CONFLICT');
        const current = await dispatch(row);
        return { draftId: current.draft_id, status: current.status };
      };
      const previous = await repo.request(requestKey);
      if (previous) return checked(previous);
      if (!port || input.db.dbType === 'noop') throw new WorkflowAiDraftError('CAPABILITY_UNAVAILABLE');
      const target = await resolveTarget();
      // Resolve may be slow. Recheck the live grant before persisting any task.
      await access(actorId, workflowId);
      const frozen: WorkflowAiDraftEnvelope = { schemaVersion: 'workflow-yaml-draft/v1', taskType: 'workflow_yaml_draft',
        taskId: randomUUID(), target,
        input: { schemaVersion: 'workflow-yaml-draft-input/v1', workflowId, mode: request.mode, prompt: request.prompt,
          workingCopy: { yaml: request.currentYaml, sha256: aiDraftSha256(request.currentYaml) } } };
      let row: AiDraftRow;
      try { row = await repo.insert({ requestKey, digest, actorId, envelope: frozen, now: now() }); }
      catch (error) {
        if (!/UNIQUE constraint failed|Duplicate entry/i.test(String(error)) && !['SQLITE_CONSTRAINT_UNIQUE', 'ER_DUP_ENTRY'].includes((error as { code?: string }).code ?? '')) throw error;
        const raced = await repo.request(requestKey);
        if (!raced) throw error;
        return checked(raced);
      }
      return checked(row);
    },
    async get(actorId, workflowId, draftId) {
      const row = await reconcileRow(await owned(actorId, workflowId, draftId));
      await access(actorId, workflowId);
      return detail(row);
    },
    async cancel(actorId, workflowId, draftId) {
      let row = await owned(actorId, workflowId, draftId);
      for (let attempt = 0; attempt < MAX_CAS_ATTEMPTS && !terminal(row); attempt++) row = await finish(row, 'cancelled', null);
      if (!terminal(row)) throw new WorkflowAiDraftError('STATE_CONFLICT');
      await access(actorId, workflowId);
      return detail(row);
    },
    async reconcile(limit = 20) {
      if (!port) return 0;
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new WorkflowAiDraftError('INVALID_INPUT');
      const rows = await repo.due(now(), limit);
      // Bound independent provider work and let other tasks progress after a
      // read/DB failure, while still surfacing persistence failures to the host.
      const failures: unknown[] = [];
      let next = 0;
      await Promise.all(Array.from({ length: Math.min(rows.length, RECONCILE_CONCURRENCY) }, async () => {
        while (next < rows.length) {
          const row = rows[next++];
          try { await reconcileRow(row); } catch (error) { failures.push(error); }
        }
      }));
      if (failures.length) throw new Error('AI_DRAFT_RECONCILIATION_FAILED');
      return rows.length;
    },
  };
}
