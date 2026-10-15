import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RepairBatchError } from '../contracts/repair-batch.js';
import type { RepairDeployment } from '../contracts/repair-release.js';
import type { RepairVerificationEvidence, RepairVerificationConclusion } from '../contracts/repair-verification.js';
import { RepairReleaseRepository } from '../repositories/repair-release-repository.js';
import { RepairBatchRepository } from '../repositories/repair-batch-repository.js';

/** Existing real runs only. Never trigger business executions or equate a
 * successful generation/deployment with a verified repair. Routes require full
 * workflow view access; limited Bot viewers cannot read aggregate evidence. */
export function createRepairVerificationService(db: IDatabase) {
  async function deployment(taskId: string, id: string, connection = db) {
    const value = (await new RepairReleaseRepository(connection).list<RepairDeployment>(taskId, 'deployment')).find(row => row.id === id);
    if (!value || value.status !== 'succeeded' || !value.receipt) throw new RepairBatchError('STATE_CONFLICT', '需要先确认候选已真实部署，才能验证效果');
    return value;
  }
  return {
    async evidence(taskId: string, id: string): Promise<RepairVerificationEvidence> {
      const d = await deployment(taskId, id), number = d.receipt!.deployNumber;
      const samples = await db.query<{ flow_id: string; status: string; started_at: number; completed_at: number | null }>(
        'SELECT flow_id, status, started_at, completed_at FROM flow_runs WHERE workflow_id = ? AND workflow_deploy_number = ? ORDER BY started_at DESC LIMIT 50', [d.workflowId, number]);
      const counts = await db.query<{ total: number; succeeded: number; failed: number; deployment: number; completed: number;
        first_started: number | null; last_started: number | null; average_duration: number | null; average_tokens: number | null }>(
        `SELECT workflow_deploy_number AS deployment, COUNT(*) AS total,
          MIN(started_at) AS first_started, MAX(started_at) AS last_started, AVG(total_duration_ms) AS average_duration,
          AVG(total_token_usage) AS average_tokens, SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed,
          SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed FROM flow_runs
          WHERE workflow_id = ? AND workflow_deploy_number IN (?, ?) GROUP BY workflow_deploy_number`, [d.workflowId, number, d.baselineDeployNumber]);
      const current = counts.find(row => Number(row.deployment) === number), baseline = counts.find(row => Number(row.deployment) === d.baselineDeployNumber);
      const unknown = (await db.query<{ total: number }>('SELECT COUNT(*) AS total FROM flow_runs WHERE workflow_id = ? AND workflow_deploy_number IS NULL', [d.workflowId]))[0];
      const revision = await new RepairBatchRepository(db).getRevision(d.workflowId, taskId, d.revision);
      const signatures = [...new Set((revision?.input.items ?? []).map(item => item.context?.signature).filter((signature): signature is string => typeof signature === 'string'))];
      const diagnoses = !signatures.length ? [] : await db.query<{ failure_signature: string; matching_runs: number }>(
        `SELECT hd.failure_signature, COUNT(DISTINCT hd.flow_id) AS matching_runs
          FROM workflow_healing_diagnoses hd JOIN flow_runs fr ON fr.flow_id = hd.flow_id AND fr.workflow_id = hd.workflow_id
          WHERE fr.workflow_id = ? AND fr.workflow_deploy_number = ? AND hd.failure_signature IN (${signatures.map(() => '?').join(',')})
          GROUP BY hd.failure_signature`, [d.workflowId, number, ...signatures]);
      const diagnosed = (await db.query<{ total: number }>(`SELECT COUNT(DISTINCT hd.flow_id) AS total FROM workflow_healing_diagnoses hd
        JOIN flow_runs fr ON fr.flow_id = hd.flow_id AND fr.workflow_id = hd.workflow_id WHERE fr.workflow_id = ? AND fr.workflow_deploy_number = ?`, [d.workflowId, number]))[0];
      const timeouts = (await db.query<{ total: number }>(`SELECT COUNT(DISTINCT hd.flow_id) AS total FROM workflow_healing_diagnoses hd
        JOIN flow_runs fr ON fr.flow_id = hd.flow_id AND fr.workflow_id = hd.workflow_id WHERE fr.workflow_id = ? AND fr.workflow_deploy_number = ? AND hd.failure_mode = 'timeout'`, [d.workflowId, number]))[0];
      const recurrence = (revision?.input.items ?? []).map(item => {
        const signature = typeof item.context?.signature === 'string' ? item.context.signature : null;
        return { itemId: item.itemId, signature, matchingRuns: signature ? diagnoses.filter(row => row.failure_signature === signature).reduce((total, row) => total + Number(row.matching_runs), 0) : null,
          diagnosedRuns: Number(diagnosed?.total ?? 0) };
      });
      const nodeRows = !samples.length ? [] : await db.query<{ flow_id: string; node_id: string; status: string }>(
        `SELECT flow_id, node_id, status FROM (SELECT flow_id, node_id, status,
          ROW_NUMBER() OVER (PARTITION BY flow_id ORDER BY started_at DESC) AS row_rank FROM node_executions
          WHERE flow_id IN (${samples.map(() => '?').join(',')})) recent WHERE row_rank <= 200`, samples.map(sample => sample.flow_id));
      const rows = [];
      for (const sample of samples) {
        const nodes = nodeRows.filter(row => row.flow_id === sample.flow_id);
        rows.push({ flowId: sample.flow_id, status: sample.status, deployNumber: number, startedAt: sample.started_at,
          completedAt: sample.completed_at, nodes: nodes.map(node => ({ nodeId: node.node_id, status: node.status })) });
      }
      return { deploymentId: id, deployNumber: number, candidateCommit: d.candidateCommit, samples: rows,
        excludedUnknownVersions: Number(unknown?.total ?? 0), recurrence,
        metrics: { window: { from: current?.first_started == null ? null : Number(current.first_started), to: current?.last_started == null ? null : Number(current.last_started) },
          succeeded: Number(current?.succeeded ?? 0), failed: Number(current?.failed ?? 0), completed: Number(current?.completed ?? 0),
          successRate: current?.completed ? Number(current.succeeded) / Number(current.completed) : null,
          averageDurationMs: current?.average_duration == null ? null : Number(current.average_duration), averageTokens: current?.average_tokens == null ? null : Number(current.average_tokens),
          diagnosedTimeoutRuns: Number(timeouts?.total ?? 0), diagnosedRuns: Number(diagnosed?.total ?? 0) },
        total: Number(current?.total ?? 0), baseline: { deployNumber: d.baselineDeployNumber, total: Number(baseline?.total ?? 0),
          succeeded: Number(baseline?.succeeded ?? 0), failed: Number(baseline?.failed ?? 0),
          successRate: baseline?.completed ? Number(baseline.succeeded) / Number(baseline.completed) : null,
          averageDurationMs: baseline?.average_duration == null ? null : Number(baseline.average_duration),
          averageTokens: baseline?.average_tokens == null ? null : Number(baseline.average_tokens),
          window: { from: baseline?.first_started == null ? null : Number(baseline.first_started), to: baseline?.last_started == null ? null : Number(baseline.last_started) } },
        status: !samples.length ? 'waiting' : samples.some(row => row.completed_at != null) ? 'ready' : 'collecting',
        note: '仅统计明确记录此部署编号的运行；旧版本及版本不明的运行不纳入。运行成功不等于原问题已消失，需核对相关节点和原始证据。' };
    },
    async conclusions(taskId: string) { return new RepairReleaseRepository(db).list<RepairVerificationConclusion>(taskId, 'verification'); },
    async reopen(actorId: string, taskId: string, id: string, request: { requestId: string; itemId: string; expectedStateVersion: number }) {
      if (!actorId || !request.requestId?.trim()) throw new RepairBatchError('INVALID_INPUT', '缺少重新修复请求身份');
      return db.transaction(async tx => {
        const d = await deployment(taskId, id, tx);
        await new RepairBatchRepository(tx).lockWorkflow(tx, d.workflowId);
        const records = new RepairReleaseRepository(tx), payload = { actorId, deploymentId: id, ...request };
        const prior = await records.retry(taskId, 'verification_reopen', payload);
        if (prior) return prior;
        const conclusions = await records.list<RepairVerificationConclusion>(taskId, 'verification');
        if (!conclusions.some(row => row.deploymentId === id && row.itemId === request.itemId && row.result === 'ineffective')) throw new RepairBatchError('STATE_CONFLICT', '只有明确记录仍未解决的处理项才能重新修复');
        const updated = await tx.exec(`UPDATE workflow_repair_items SET state = 'pending', active_task_id = NULL, active_revision = NULL,
          state_version = state_version + 1, updated_at_ms = ? WHERE item_id = ? AND workflow_id = ? AND state = 'ineffective'
          AND active_task_id = ? AND active_revision = ? AND state_version = ?`, [Date.now(), request.itemId, d.workflowId, taskId, d.revision, request.expectedStateVersion]);
        if (updated.affectedRows !== 1) throw new RepairBatchError('STATE_CONFLICT', '处理项已变化，请刷新后核对');
        const event = { id: randomUUID(), workflowId: d.workflowId, taskId, revision: d.revision, stateVersion: 0,
          itemId: request.itemId, actorId, createdAtMs: Date.now() };
        await records.insert('verification_reopen', payload, event);
        return event;
      });
    },
    async conclude(actorId: string, taskId: string, id: string, request: { requestId: string; itemId: string; expectedStateVersion: number;
      result: 'verified' | 'ineffective' | 'inconclusive'; reason: string; flowIds: string[] }) {
      if (!actorId || !request.requestId?.trim() || !request.reason?.trim() || request.reason.length > 4000
        || !['verified', 'ineffective', 'inconclusive'].includes(request.result) || !Array.isArray(request.flowIds)
        || request.flowIds.length > 50 || request.result !== 'inconclusive' && !request.flowIds.length) throw new RepairBatchError('INVALID_INPUT', '请提供明确结论、依据以及本部署的运行样本');
      return db.transaction(async tx => {
        const d = await deployment(taskId, id, tx);
        await new RepairBatchRepository(tx).lockWorkflow(tx, d.workflowId);
        const records = new RepairReleaseRepository(tx);
        const payload = { actorId, deploymentId: id, ...request };
        const previous = await records.retry<RepairVerificationConclusion>(taskId, 'verification', payload);
        if (previous) return previous;
        const item = (await tx.query<{ state: string; state_version: number; active_task_id: string; active_revision: number }>(
          'SELECT state, state_version, active_task_id, active_revision FROM workflow_repair_items WHERE item_id = ? AND workflow_id = ?', [request.itemId, d.workflowId]))[0];
        if (!item || item.state !== 'awaiting_verification' || item.active_task_id !== taskId || Number(item.active_revision) !== d.revision
          || Number(item.state_version) !== request.expectedStateVersion) throw new RepairBatchError('STATE_CONFLICT', '处理项或验证版本已变化，请刷新后核对');
        const flowIds = [...new Set(request.flowIds)];
        if (flowIds.length) {
          const valid = await tx.query<{ flow_id: string }>(`SELECT flow_id FROM flow_runs WHERE workflow_id = ? AND workflow_deploy_number = ?
            AND completed_at IS NOT NULL AND flow_id IN (${flowIds.map(() => '?').join(',')})`, [d.workflowId, d.receipt!.deployNumber, ...flowIds]);
          if (valid.length !== flowIds.length) throw new RepairBatchError('STATE_CONFLICT', '样本不是此部署的已完成运行，不能用于效果结论');
        }
        const conclusion: RepairVerificationConclusion = { id: randomUUID(), workflowId: d.workflowId, taskId, revision: d.revision,
          stateVersion: 0, deploymentId: id, deployNumber: d.receipt!.deployNumber, itemId: request.itemId, actorId,
          result: request.result, reason: request.reason.trim(), flowIds, createdAtMs: Date.now() };
        await records.insert('verification', payload, conclusion);
        if (request.result !== 'inconclusive') await tx.exec(`UPDATE workflow_repair_items SET state = ?, state_version = state_version + 1,
          updated_at_ms = ? WHERE item_id = ? AND workflow_id = ? AND state_version = ?`, [request.result, Date.now(), request.itemId, d.workflowId, request.expectedStateVersion]);
        return conclusion;
      });
    },
  };
}
