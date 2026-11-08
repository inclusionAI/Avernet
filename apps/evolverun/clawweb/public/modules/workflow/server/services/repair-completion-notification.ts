import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { RepairRevision } from '../contracts/repair-batch.js';
import { WorkflowAutomationRepository } from '../repositories/workflow-automation-repository.js';
import { RepairBatchRepository } from '../repositories/repair-batch-repository.js';

/** Enqueue in the result transaction; remote delivery never delays AIS completion. */
export async function queueRepairCompletion(tx: IDatabase, revision: RepairRevision) {
  console.info('[workflow-notification] ' + JSON.stringify({ event: 'completion_observed', workflowId: revision.workflowId,
    taskId: revision.taskId, revision: revision.revision, phase: revision.phase, mode: revision.input.automation ? 'automatic' : 'manual' }));
  // Automatic success is announced once its complete batch chain is checked by the coordinator.
  const origin = revision.input.automation;
  if (origin && ['review', 'no_change'].includes(revision.phase)) {
    if (revision.phase === 'review' || origin.batchIndex !== undefined && origin.batchIndex + 1 < origin.batchCount!) return;
    const chain = await new RepairBatchRepository(tx).listRevisions(revision.workflowId, revision.taskId);
    if (chain.some(row => (row.draft?.itemResults as Array<{ status: string }> | undefined)?.some(item => item.status === 'changed'))) return;
  }
  const title = revision.phase === 'review' ? '优化草稿已可审阅'
    : revision.phase === 'no_change' ? '优化任务已完成，无需修改'
    : revision.phase === 'blocked' ? '优化草稿检查未通过' : '优化任务生成失败';
  await new WorkflowAutomationRepository(tx).ensure(revision.workflowId, 'auto_notice', `draft:${revision.taskId}:${revision.revision}`, {
    title, text: `工作流 ${revision.workflowId} · 候选 v${revision.revision}\n${revision.phase === 'review' ? '草稿已生成，请审阅变更后决定是否发布。' : '请打开任务查看结果和处理详情。'}`,
    kind: 'completion', taskId: revision.taskId, revision: revision.revision,
    path: `/workflows/workspace?workflowId=${encodeURIComponent(revision.workflowId)}&tab=evolution&repairTaskId=${encodeURIComponent(revision.taskId)}`,
  }, Date.now());
}
