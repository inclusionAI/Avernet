import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { validateWorkflowEvolutionAnalysisResult } from '@avernet/clawevolve/server/services/evolution/contracts';
import type { RepairItem } from '../contracts/repair-batch.js';
import { digestRepairJson } from '../contracts/repair-batch.js';
import type { RepairDeployment, RepairPublicationPort } from '../contracts/repair-release.js';
import type { RepairEffectRule, RepairVerificationConclusion } from '../contracts/repair-verification.js';
import { RepairBatchRepository } from '../repositories/repair-batch-repository.js';
import { RepairReleaseRepository } from '../repositories/repair-release-repository.js';
export type EffectSample = { flowId: string; deployNumber: number; completed: boolean; nodeStatus: string; analysisComplete: boolean; signatures: string[] };
export type RepairEffectDecision = { result: 'observing' | 'verified' | 'ineffective'; reason: string; eligibleSamples: number; flowIds: string[]; rule: RepairEffectRule | null; observedDeployNumber?: number };
/** v1 intentionally handles objectively diagnosed node timeouts only. Quality, cost,
 * absent node identity and arbitrary text have no automatic success verifier. */
export function effectRule(item: RepairItem): RepairEffectRule | null {
  const context = item.context;
  return context?.failureMode === 'timeout' && typeof context.nodeId === 'string' && !!context.nodeId
    && typeof context.signature === 'string' && !!context.signature ? { version: 'node-timeout/v1', nodeId: context.nodeId,
      signature: context.signature, windowDays: 7, minimumSamples: 5 } : null;
}
export function decideRepairEffect(rule: RepairEffectRule | null, deployNumber: number, input: EffectSample[]): RepairEffectDecision {
  const samples = [...new Map(input.filter(row => row.deployNumber === deployNumber && row.completed
    && ['succeeded', 'failed', 'timeout', 'timed_out'].includes(row.nodeStatus)).map(row => [row.flowId, row])).values()];
  const base = { rule, eligibleSamples: samples.length, flowIds: samples.map(row => row.flowId).sort() };
  if (!rule) return { ...base, result: 'observing', reason: '缺少适用于此问题的自动验证规则' };
  const recurring = samples.filter(row => row.signatures.includes(rule.signature));
  if (recurring.length) return { ...base, result: 'ineffective', flowIds: recurring.map(row => row.flowId).sort(), reason: '生效版本的相关节点诊断明确复现原问题' };
  const clean = samples.filter(row => row.nodeStatus === 'succeeded' && row.analysisComplete);
  if (clean.length < rule.minimumSamples || clean.length !== samples.length) return { ...base, result: 'observing', reason: `相关节点有效且已分析样本 ${clean.length}/${rule.minimumSamples}，继续观察；失败或未分析不视为通过` };
  return { ...base, result: 'verified', reason: `${rule.version}：7天内至少${rule.minimumSamples}次相关节点成功且完整分析未发现原签名；仅证明此超时问题满足规则` };
}

export function createRepairAutoVerification(db: IDatabase, publication?: RepairPublicationPort) {
  async function decisions(d: RepairDeployment) {
    const revision = await new RepairBatchRepository(db).getRevision(d.workflowId, d.taskId, d.revision);
    const changed = new Set(Array.isArray(revision?.draft?.itemResults) ? revision.draft.itemResults.filter(row => row.status === 'changed').map(row => row.itemId) : []);
    const result: Array<RepairEffectDecision & { itemId: string }> = [];
    const active = changed.size ? (await db.query<{ deploy_number: number }>('SELECT deploy_number FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [d.workflowId]))[0] : null;
    const versionChanged = changed.size > 0 && Number(active?.deploy_number) !== d.receipt!.deployNumber;
    const applied = versionChanged && publication?.appliedState ? await publication.appliedState(d) : null;
    for (const item of revision?.input.items ?? []) {
      if (!changed.has(item.itemId)) continue;
      const rule = effectRule(item);
      if (versionChanged && applied?.status !== 'unchanged' && applied?.itemStates?.[item.itemId] !== 'unchanged') { result.push({ itemId: item.itemId, ...decideRepairEffect(rule, d.receipt!.deployNumber, []), reason: '当前生效版本已变化；重新核对已应用修改，旧版本证据不形成当前效果结论' }); continue; }
      const observedDeployNumber = versionChanged ? applied!.activeDeployNumber : d.receipt!.deployNumber;
      if (!rule) { result.push({ itemId: item.itemId, ...decideRepairEffect(null, d.receipt!.deployNumber, []) }); continue; }
      const rows = await db.query<{ flow_id: string; status: string; completed_at: number | null; node_status: string }>(
        `SELECT fr.flow_id, fr.status, fr.completed_at, ne.status AS node_status FROM flow_runs fr
         JOIN node_executions ne ON ne.flow_id = fr.flow_id AND ne.node_id = ?
         WHERE fr.workflow_id = ? AND fr.workflow_deploy_number = ? AND fr.started_at >= ? ORDER BY fr.started_at DESC, ne.started_at DESC LIMIT 2000`,
        [rule.nodeId, d.workflowId, observedDeployNumber, Math.floor((Date.now() - rule.windowDays * 86_400_000) / 1000)]);
      if (rows.length === 2000) { result.push({ itemId: item.itemId, ...decideRepairEffect(rule, d.receipt!.deployNumber, []), reason: '样本超过单轮验证上限，需缩小观察范围' }); continue; }
      const samples = new Map<string, EffectSample>();
      for (const row of rows) if (!samples.has(row.flow_id)) samples.set(row.flow_id, { flowId: row.flow_id,
        deployNumber: observedDeployNumber, completed: row.completed_at != null, nodeStatus: row.node_status, analysisComplete: false, signatures: [] });
      // Only the latest completed, validated analysis of a run can prove absence.
      const analyses = await db.query<{ flow_id: string | null; scope_json: string; result_json: string }>(
        `SELECT flow_id, scope_json, result_json FROM workflow_evolution_analysis_runs WHERE workflow_id = ?
         AND status = 'completed' AND scope_type <> 'issue_aggregate' ORDER BY completed_at_ms DESC, id DESC LIMIT 2000`, [d.workflowId]);
      const visited = new Set<string>();
      for (const row of analyses) {
        let declared: string[] = row.flow_id ? [row.flow_id] : [];
        try {
          const frozenScope = JSON.parse(row.scope_json);
          if (Array.isArray(frozenScope.flowIds)) declared.push(...frozenScope.flowIds.filter((id: unknown): id is string => typeof id === 'string'));
          const analysis = validateWorkflowEvolutionAnalysisResult(JSON.parse(row.result_json));
          const scope = JSON.parse(row.scope_json);
          if (scope.nodeId && scope.nodeId !== rule.nodeId || scope.nodeIds?.length && !scope.nodeIds.includes(rule.nodeId)) continue;
          for (const id of [...(row.flow_id ? [row.flow_id] : []), ...(Array.isArray(scope.flowIds) ? scope.flowIds : []), ...analysis.diagnoses.flatMap(value => value.flowIds)]) {
            if (visited.has(id)) continue;
            visited.add(id); const sample = samples.get(id); if (!sample) continue;
            sample.analysisComplete = analysis.unknowns.length === 0;
            sample.signatures = analysis.diagnoses.filter(value => value.flowIds.includes(id) && value.nodeId === rule.nodeId).map(value => value.failureSignature);
          }
        } catch { for (const id of declared.length ? declared : samples.keys()) visited.add(id); /* Invalid latest analysis cannot establish absence. */ }
      }
      result.push({ itemId: item.itemId, observedDeployNumber, ...decideRepairEffect(rule, observedDeployNumber, [...samples.values()]) });
    }
    return result;
  }
  return { decisions, async observe(d: RepairDeployment) {
    if (d.status !== 'succeeded' || !d.receipt) return;
    const watched = await db.query('SELECT item_id FROM workflow_repair_items WHERE workflow_id = ? AND active_task_id = ? AND active_revision = ? AND state IN (?, ?)',
      [d.workflowId, d.taskId, d.revision, 'awaiting_verification', 'verified']);
    if (!watched.length) return;
    const assessments = await decisions(d);
    const active = (await db.query<{ deploy_number: number }>('SELECT deploy_number FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [d.workflowId]))[0];
    if (Number(active?.deploy_number) !== d.receipt.deployNumber) {
      if (!publication?.appliedState) return;
      const actual = await publication.appliedState(d);
      if (actual.status === 'changed') for (const assessment of assessments.filter(row => actual.itemStates?.[row.itemId] === 'changed')) await db.transaction(async tx => {
        const repo = new RepairBatchRepository(tx); await repo.lockWorkflow(tx, d.workflowId);
        const current = await repo.getItem(d.workflowId, assessment.itemId);
        if (!current || current.activeTaskId !== d.taskId || !['verified', 'awaiting_verification'].includes(current.state)) return;
        if ((await repo.listItems(d.workflowId)).some(item => item.previousItemId === current.itemId)) return;
        const stillActive = (await tx.query<{ deploy_number: number }>('SELECT deploy_number FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [d.workflowId]))[0];
        if (Number(stillActive?.deploy_number) !== actual.activeDeployNumber) return;
        const records = new RepairReleaseRepository(tx);
        const payload = { requestId: digestRepairJson(['reassess', d.id, current.itemId, actual.activeDeployNumber]), itemId: current.itemId };
        if (await records.retry(d.taskId, 'verification_reassessment', payload)) return;
        const recurrence = { episodeKey: payload.requestId, reason: 'baseline_changed', deploymentId: d.id, activeDeployNumber: actual.activeDeployNumber, packCommit: actual.packCommit, flowIds: [] };
        const content = { itemId: current.itemId, groupKey: current.groupKey, proposalKey: current.proposalKey, contentRevision: current.contentRevision,
          previousItemId: current.previousItemId, proposal: current.proposal, instruction: current.instruction, sources: current.sources,
          context: { ...current.context, recurrence } };
        await records.insert('verification_reassessment', payload, { id: randomUUID(), workflowId: d.workflowId, taskId: d.taskId, revision: d.revision,
          stateVersion: 0, itemId: current.itemId, reason: '当前生效 Pack 已改变已审阅的具体文件，重新评估；不复用旧补丁', createdAtMs: Date.now() });
        const updated = await tx.exec('UPDATE workflow_repair_items SET content_json = ?, state_version = state_version + 1, updated_at_ms = ? WHERE item_id = ? AND workflow_id = ? AND state_version = ?',
          [JSON.stringify(content), Date.now(), current.itemId, d.workflowId, current.stateVersion]);
        if (updated.affectedRows !== 1) throw new Error('REASSESSMENT_ITEM_STATE_CHANGED');
      });
    }
    for (const assessment of assessments) {
      if (assessment.result === 'observing') continue;
      const result = assessment.result;
      await db.transaction(async tx => {
        const repo = new RepairBatchRepository(tx); await repo.lockWorkflow(tx, d.workflowId);
        const current = await repo.getItem(d.workflowId, assessment.itemId);
        if (!current || current.activeTaskId !== d.taskId || current.activeRevision !== d.revision
          || !['awaiting_verification', 'verified'].includes(current.state)) return;
        const records = new RepairReleaseRepository(tx);
        const payload = { requestId: digestRepairJson(['auto-effect', d.id, assessment.itemId, assessment.result, assessment.observedDeployNumber ?? d.receipt!.deployNumber]), deploymentId: d.id, itemId: assessment.itemId };
        if (await records.retry(d.taskId, 'verification', payload)) return;
        const active = (await tx.query<{ deploy_number: number }>('SELECT deploy_number FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [d.workflowId]))[0];
        if (Number(active?.deploy_number) !== (assessment.observedDeployNumber ?? d.receipt!.deployNumber)) return;
        const event: RepairVerificationConclusion = { id: randomUUID(), workflowId: d.workflowId, taskId: d.taskId, revision: d.revision,
          stateVersion: 0, deploymentId: d.id, deployNumber: assessment.observedDeployNumber ?? d.receipt!.deployNumber, itemId: assessment.itemId, actorId: 'system:verification',
          result, reason: assessment.reason, flowIds: assessment.flowIds.slice(0, 50), rule: assessment.rule!,
          eligibleSamples: assessment.eligibleSamples, createdAtMs: Date.now() };
        await records.insert('verification', payload, event);
        // Preserve immutable prior conclusions. Recurrence opens optimization again;
        // source evidence changes are required before the original proposal can run again.
        const content = { itemId: current.itemId, groupKey: current.groupKey, proposalKey: current.proposalKey,
          contentRevision: current.contentRevision, previousItemId: current.previousItemId, proposal: current.proposal,
          instruction: current.instruction, sources: current.sources, context: { ...current.context,
            ...(result === 'ineffective' ? { recurrence: { episodeKey: digestRepairJson(['recurrence', d.id, current.itemId]),
              deploymentId: d.id, candidateCommit: d.candidateCommit, flowIds: assessment.flowIds.slice(0, 50) } } : {}) } };
        const updated = await tx.exec(`UPDATE workflow_repair_items SET state = ?, content_json = ?, state_version = state_version + 1, updated_at_ms = ?
          WHERE workflow_id = ? AND item_id = ? AND state_version = ?`,
        [result, JSON.stringify(content), Date.now(), d.workflowId, assessment.itemId, current.stateVersion]);
        if (updated.affectedRows !== 1) throw new Error('VERIFICATION_ITEM_STATE_CHANGED');
      });
    }
  }, async reconcile() {
    let after = '', count = 0;
    for (;;) {
      const rows = await db.query<{ record_id: string; body_json: string }>(`SELECT record_id, body_json FROM workflow_repair_release_records
        WHERE record_kind = 'deployment' AND record_id > ? ORDER BY record_id LIMIT 100`, [after]);
      if (!rows.length) break;
      for (const row of rows) {
        const d = JSON.parse(row.body_json) as RepairDeployment;
        if (d.status === 'succeeded' && d.receipt) await this.observe(d);
      }
      count += rows.length; after = rows.at(-1)!.record_id;
    }
    return count;
  } };
}
