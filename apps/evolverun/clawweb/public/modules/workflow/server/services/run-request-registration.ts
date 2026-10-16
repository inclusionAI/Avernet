import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RepairBatchError } from '../contracts/repair-batch.js';
import type { RepairTrial } from '../contracts/repair-trial.js';
import { RepairBatchRepository } from '../repositories/repair-batch-repository.js';
import { RepairReleaseRepository } from '../repositories/repair-release-repository.js';
/** The normal run INSERT owns both request idempotence and purpose registration. */
export async function registerRunRequest(db: IDatabase, body: Record<string, unknown>, insert: (tx: IDatabase) => Promise<void>): Promise<'created' | 'existing' | null> {
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
    if (typeof body.flow_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(body.flow_id) || request.status === 'failed'
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
