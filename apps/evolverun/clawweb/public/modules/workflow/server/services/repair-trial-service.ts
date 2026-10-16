import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RepairBatchError, digestRepairJson, type RepairRevision } from '../contracts/repair-batch.js';
import type { RepairWorkbenchService } from '../contracts/repair-workbench.js';
import type { RepairTrial, RepairTrialCandidate, RepairTrialPort, RepairTrialRequest } from '../contracts/repair-trial.js';
import { RepairReleaseRepository } from '../repositories/repair-release-repository.js';
import { RepairBatchRepository } from '../repositories/repair-batch-repository.js';
import { redactTrial, trialSensitiveValues, validateTrialInput } from './repair-trial-input.js';

function conflict(message: string): never { throw new RepairBatchError('STATE_CONFLICT', message); }
function candidate(workflowId: string, taskId: string, revision: RepairRevision): RepairTrialCandidate {
  const { candidateCommit, packDigest } = revision.draft ?? {};
  if (typeof candidateCommit !== 'string' || !/^[a-f0-9]{40}(?:[a-f0-9]{24})?$/.test(candidateCommit)
    || typeof packDigest !== 'string' || !/^[a-f0-9]{40}(?:[a-f0-9]{24})?$/.test(packDigest)) conflict('候选 Git 身份不完整');
  return { workflowId, taskId, revision: revision.revision, candidateCommit, packDigest, baseline: revision.input.baseline };
}
export function createRepairTrialService(input: { db: IDatabase; workbench: RepairWorkbenchService; port?: RepairTrialPort }) {
  const { db, workbench } = input, records = new RepairReleaseRepository(db);
  const port = () => { if (!input.port) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '宿主尚未接入精确候选试跑'); return input.port; };
  async function lock(tx: IDatabase, taskId: string) {
    const batches = new RepairBatchRepository(tx), workflowId = await batches.taskWorkflow(taskId);
    if (!workflowId) throw new RepairBatchError('TASK_NOT_FOUND', '修复任务不存在');
    await batches.lockWorkflow(tx, workflowId);
    return { batches, workflowId, records: new RepairReleaseRepository(tx) };
  }
  async function find(id: string, tx = db): Promise<RepairTrial> {
    const rows = await tx.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_id = ? AND record_kind = 'trial'", [id]);
    if (!rows.length) conflict('试跑请求不存在');
    return JSON.parse(rows[0].body_json);
  }
  async function authorized(trial: RepairTrial) {
    if (!(await port().targets(trial.workflowId, trial.actorId)).some(t => t.id === trial.target.id && t.botId === trial.target.botId && t.ownerId === trial.target.ownerId && t.environment === 'prod')) conflict('Bot 执行权限或运行能力已变化');
  }
  async function view(trial: RepairTrial) {
    const { input: rawInput, ...rest } = trial;
    const values = trialSensitiveValues(rawInput);
    const result: RepairTrial = { ...rest, input: redactTrial(rawInput) as Record<string, unknown> };
    // Only the atomically registered flowId can prove progress; never infer from chat or latest workflow run.
    const row = trial.flowId ? (await db.query<{ workflow_id: string; origin_bot_id: string; status: string; result_json: string | null; total_duration_ms: number | null }>(
      'SELECT workflow_id, origin_bot_id, status, result_json, total_duration_ms FROM flow_runs WHERE flow_id = ?', [trial.flowId]))[0] : null;
    if (row && row.workflow_id === trial.workflowId && row.origin_bot_id === `${trial.target.botId}:${trial.target.ownerId}`) {
      result.status = row.status === 'succeeded' ? 'succeeded' : ['failed', 'aborted', 'cancelled', 'canceled'].includes(row.status) ? 'failed'
        : ['waiting', 'blocked', 'paused'].includes(row.status) ? 'waiting' : 'running';
      if (typeof row.result_json === 'string') {
        try { result.output = JSON.stringify(redactTrial(JSON.parse(row.result_json), values), null, 2).slice(0, 8192); }
        catch { result.output = String(redactTrial(row.result_json, values)); }
      }
      result.durationMs = row.total_duration_ms;
    }
    if (!row && !['failed', 'succeeded'].includes(result.status) && Date.now() - trial.updatedAtMs > 120_000) {
      result.status = 'unknown'; result.error = '尚未收到可信运行记录，请核对原请求；不会自动换 Bot 或重复执行';
    }
    return result;
  }
  return {
    configured: !!input.port,
    async options(actorId: string, taskId: string, number: number) {
      const task = await workbench.task(taskId), revision = task.revisions.find(row => row.revision === number);
      if (!revision) conflict('候选不存在');
      const options = await port().inspect(candidate(task.workflowId, taskId, revision));
      return { ...options, inputSchemaDigest: digestRepairJson(options.inputSchema), targets: (await port().targets(task.workflowId, actorId)).filter(t => t.environment === 'prod') };
    },
    async list(taskId: string, offset = 0) {
      const rows = await db.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE task_id = ? AND record_kind = 'trial' ORDER BY created_at_ms DESC, record_id DESC LIMIT 20 OFFSET ?", [taskId, offset]);
      return Promise.all(rows.map(row => view(JSON.parse(row.body_json))));
    },
    async start(actorId: string, taskId: string, request: RepairTrialRequest) {
      if (!request || typeof request.requestId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(request.requestId)
        || !Number.isSafeInteger(request.revision) || !Number.isSafeInteger(request.expectedStateVersion)) throw new RepairBatchError('INVALID_INPUT', '试跑请求身份无效');
      const key = { actorId, ...request }, retry = await records.retry<RepairTrial>(taskId, 'trial', key);
      if (retry) return view(retry);
      const task = await workbench.task(taskId), revision = task.latestSuccessful;
      if (!revision || revision.revision !== request.revision || revision.phase !== 'review' || revision.stateVersion !== request.expectedStateVersion || task.latestAttempt.phase === 'drafting'
        || (revision.checks?.static as { status?: string })?.status !== 'passed') conflict('候选已变化或静态检查未通过，请刷新后重试');
      const frozen = candidate(task.workflowId, taskId, revision), options = await port().inspect(frozen);
      if (digestRepairJson(options.inputSchema) !== request.inputSchemaDigest) conflict('参数定义已变化，请重新填写');
      validateTrialInput(request.input, options.inputSchema);
      const target = (await port().targets(task.workflowId, actorId)).find(t => t.id === request.targetId && t.environment === 'prod');
      if (!target) conflict('请选择已授权且支持试跑的生产 Bot');
      // Save is content-addressed and never moves the active deployment or a Bot workspace.
      await port().save(frozen, options);
      const created = await db.transaction(async tx => {
        const locked = await lock(tx, taskId), repeated = await locked.records.retry<RepairTrial>(taskId, 'trial', key);
        if (repeated) return { trial: repeated, dispatch: false };
        const fresh = await locked.batches.listRevisions(locked.workflowId, taskId), selected = fresh.find(r => r.revision === request.revision);
        if (!selected || selected.stateVersion !== request.expectedStateVersion || selected.phase !== 'review' || fresh[0].phase === 'drafting'
          || selected.draft?.candidateCommit !== frozen.candidateCommit || selected.draft?.packDigest !== frozen.packDigest
          || fresh.find(r => r.draft !== null && r.phase !== 'no_change')?.revision !== request.revision) conflict('保存期间候选已变化，请重新审阅');
        const active = (await locked.records.list<RepairTrial>(taskId, 'trial')).find(t => t.revision === request.revision && ['dispatching', 'awaiting_run', 'unknown'].includes(t.status) && !t.flowId);
        if (active) conflict('同一候选存在待核对的试跑，请先核对该请求');
        const id = randomUUID();
        const trial: RepairTrial = { ...frozen, id, flowId: null, requestId: request.requestId, actorId, target,
          stateVersion: 1, status: 'dispatching', input: request.input, inputDigest: digestRepairJson(request.input), inputSchemaDigest: request.inputSchemaDigest,
          options, error: null, createdAtMs: Date.now(), updatedAtMs: Date.now() };
        await locked.records.insert('trial', key, trial);
        return { trial, dispatch: true };
      });
      if (created.dispatch) {
        let status: RepairTrial['status'];
        try { const receipt = await port().dispatch(created.trial); status = receipt.status === 'accepted' ? 'awaiting_run' : receipt.status === 'failed' ? 'failed' : 'unknown'; }
        catch { status = 'unknown'; }
        await db.transaction(async tx => {
          const locked = await lock(tx, taskId), latest = await find(created.trial.id, tx);
          // A late message receipt must never overwrite a registered execution/result.
          if (latest.status === 'dispatching' && !latest.flowId) await locked.records.update({ ...latest, status, stateVersion: latest.stateVersion + 1, updatedAtMs: Date.now() }, latest.stateVersion);
        });
      }
      return view(await find(created.trial.id));
    },
    async resolveRunRequest(id: string, target: { botId: string; ownerId: string }) {
      const request = await find(id);
      if (target.botId !== request.target.botId || target.ownerId !== request.target.ownerId || request.status === 'failed') conflict('运行请求的 Bot 身份或状态不匹配');
      await authorized(request);
      // Repeatable signed read. Native /runs INSERT alone decides the one execution winner.
      return { requestId: request.id, purpose: 'trial' as const, workflowId: request.workflowId, packId: request.options.packId,
        candidateRef: request.options.candidateRef, candidateCommit: request.candidateCommit, packDigest: request.packDigest,
        input: request.input, inputDigest: request.inputDigest };
    },
  };
}
