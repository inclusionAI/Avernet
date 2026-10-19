import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RepairBatchError } from '../contracts/repair-batch.js';
import type { RepairTrial } from '../contracts/repair-trial.js';
import { RepairBatchRepository } from '../repositories/repair-batch-repository.js';
import { RepairReleaseRepository } from '../repositories/repair-release-repository.js';
/** The normal run INSERT owns both request idempotence and purpose registration. */
export async function registerRunRequest(db: IDatabase, body: Record<string, unknown>, insert: (tx: IDatabase) => Promise<void>): Promise<'created' | 'existing' | null> {
  if (!body.run_request_id) {
    const legacy = await registerSavedRun(db, body, insert);
    if (legacy) return legacy;
  }
  const reserved = await db.query<{ record_kind: string }>("SELECT record_kind FROM workflow_repair_release_records WHERE record_kind IN ('trial', 'trial_run') AND record_id = ?", [body.flow_id]);
  if (!body.run_request_id && !reserved.length) return null;
  const denied = (): never => { throw new RepairBatchError('STATE_CONFLICT', '运行身份与冻结请求不一致，或该请求已有运行；请核对原运行'); };
  const rows = await db.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'trial' AND record_id = ?", [body.run_request_id]);
  if (!rows.length) denied();
  const before = JSON.parse(rows[0].body_json) as RepairTrial;
  return db.transaction(async tx => {
    await new RepairBatchRepository(tx).lockWorkflow(tx, before.workflowId);
    const latest = await tx.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'trial' AND record_id = ?", [body.run_request_id]);
    const request = JSON.parse(latest[0].body_json) as RepairTrial;
    const child = typeof body.run_request_parent_flow_id === 'string';
    if (child && (!request.flowId || body.run_request_parent_flow_id !== request.flowId || body.flow_id === request.flowId
      || !request.options.workflowIds?.includes(String(body.workflow_id)))) denied();
    if (typeof body.flow_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(body.flow_id) || request.status === 'failed'
      || !child && body.workflow_id !== request.workflowId
      || body.candidate_commit !== request.candidateCommit || body.pack_digest !== request.packDigest || body.input_digest !== request.inputDigest
      || body.run_request_bot_id !== request.target.botId || body.run_request_owner_id !== request.target.ownerId
      || body.origin_bot_id !== `${request.target.botId}:${request.target.ownerId}` || body.workflow_version !== -1 || body.workflow_deploy_number != null
      || !child && request.flowId && request.flowId !== body.flow_id) denied();
    const marker = await tx.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'trial_run' AND record_id = ?", [body.flow_id]);
    const existing = await tx.query<{ flow_id: string }>('SELECT flow_id FROM flow_runs WHERE flow_id = ?', [body.flow_id]);
    if (existing.length) {
      if (!marker.length || JSON.parse(marker[0].body_json).trialId !== request.id || !child && request.flowId !== body.flow_id) denied();
      return 'existing';
    }
    if (child) {
      const root = await tx.query<{ status: string }>('SELECT status FROM flow_runs WHERE flow_id = ?', [request.flowId]);
      if (!root.length || ['succeeded', 'failed', 'aborted', 'cancelled', 'canceled'].includes(root[0].status)) denied();
    }
    const records = new RepairReleaseRepository(tx);
    await records.insert('trial_run', { requestId: `run:${request.id}:${body.flow_id}` }, {
      id: body.flow_id as string, workflowId: String(body.workflow_id), taskId: request.taskId, revision: request.revision,
      stateVersion: 1, createdAtMs: Date.now(), trialId: request.id,
    });
    await insert(tx); // Failed persistence rolls back purpose metadata and the winner together.
    if (!child) await records.update({ ...request, flowId: body.flow_id as string, stateVersion: request.stateVersion + 1, updatedAtMs: Date.now() }, request.stateVersion);
    return 'created';
  });
}

/** Legacy plugins already report their isolated session/native parent. This is
 * best-effort association, never proof that the reviewed Pack was executed. */
async function registerSavedRun(db: IDatabase, body: Record<string, unknown>, insert: (tx: IDatabase) => Promise<void>): Promise<'created' | 'existing' | null> {
  const session = typeof body.origin_session_key === 'string' ? /^agent:main:workflow-trial:([a-f0-9-]{36})$/.exec(body.origin_session_key) : null;
  let parentId: string | undefined;
  try {
    const params = typeof body.params_json === 'string' ? JSON.parse(body.params_json) : {};
    const state = JSON.parse(typeof body.state_json === 'string' ? body.state_json : params._clawmind_state ?? '{}');
    parentId = state.subworkflowMeta?.parentFlowId;
  } catch { /* Missing legacy context leaves the ordinary run unchanged. */ }
  let id = session?.[1];
  if (parentId) {
    const [marker] = await db.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'trial_run' AND record_id = ?", [parentId]);
    if (marker) id = JSON.parse(marker.body_json).trialId;
  }
  if (!id) return null;
  const [row] = await db.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'trial' AND record_id = ?", [id]);
  if (!row) return null;
  const request = JSON.parse(row.body_json) as RepairTrial;
  // Exact requests must use the new signed-context registration path.
  if (request.mode !== 'saved') throw new RepairBatchError('STATE_CONFLICT', '精确执行缺少冻结请求登记身份');
  if (typeof body.flow_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(body.flow_id)
    || !parentId && (body.workflow_id !== request.workflowId || body.workflow_version != null && body.workflow_version !== -1)
    || body.workflow_deploy_number != null
    || body.origin_bot_id && ![request.target.botId, `${request.target.botId}:${request.target.ownerId}`].includes(String(body.origin_bot_id))) {
    throw new RepairBatchError('STATE_CONFLICT', '兼容执行的运行身份不匹配');
  }
  return db.transaction(async tx => {
    await new RepairBatchRepository(tx).lockWorkflow(tx, request.workflowId);
    const [latest] = await tx.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_id = ? AND record_kind = 'trial'", [id]);
    const current = JSON.parse(latest.body_json) as RepairTrial;
    const existing = await tx.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_id = ? AND record_kind = 'trial_run'", [body.flow_id]);
    if (existing.length) {
      if (JSON.parse(existing[0].body_json).trialId !== id) throw new RepairBatchError('STATE_CONFLICT', '运行身份已被占用');
      return 'existing';
    }
    const records = new RepairReleaseRepository(tx);
    await records.insert('trial_run', { requestId: `run:${id}:${body.flow_id}` }, {
      id: body.flow_id as string, workflowId: String(body.workflow_id), taskId: current.taskId, revision: current.revision,
      stateVersion: 1, createdAtMs: Date.now(), trialId: id,
    });
    await insert(tx);
    // Old engines may execute duplicate deliveries despite a rejected INSERT.
    // Persist and isolate every observed run, retaining the first root for history.
    if (!parentId && !current.flowId) await records.update({ ...current, flowId: body.flow_id as string, stateVersion: current.stateVersion + 1, updatedAtMs: Date.now() }, current.stateVersion);
    return 'created';
  });
}
