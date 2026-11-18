import { demoWorkflows } from './demoWorkflows';
import type { Run } from './types';

const runRows: [string, string, Run['status'], number, string, string, string][] = [
  ['run-1117-018', 'wf-daily', 'running', 126, '2026-11-17T10:06:00+08:00', '定时触发', '生成经营摘要'],
  ['run-1117-017', 'wf-ticket', 'failed', 522, '2026-11-17T10:03:00+08:00', '事件触发', '诉求分类'],
  ['run-1117-016', 'wf-contract', 'success', 438, '2026-11-17T10:01:00+08:00', '手动触发', '人工复核交接'],
  ['run-1117-015', 'wf-feedback', 'waiting', 83, '2026-11-17T09:58:00+08:00', '定时触发', '等待人工确认'],
  ['run-1117-014', 'wf-market', 'running', 276, '2026-11-17T09:55:00+08:00', '手动触发', '趋势归纳'],
  ['run-1117-013', 'wf-content', 'success', 391, '2026-11-17T09:51:00+08:00', '事件触发', '检查完成'],
  ['run-1117-012', 'wf-daily', 'success', 452, '2026-11-17T09:45:00+08:00', '定时触发', '日报归档'],
  ['run-1117-011', 'wf-feedback', 'failed', 601, '2026-11-17T09:39:00+08:00', '手动触发', '反馈主题聚类'],
  ['run-1117-010', 'wf-ticket', 'waiting', 62, '2026-11-17T09:36:00+08:00', '事件触发', '等待人工分派'],
  ['run-1117-009', 'wf-contract', 'cancelled', 174, '2026-11-17T09:31:00+08:00', '手动触发', '操作人取消'],
  ['run-1117-008', 'wf-market', 'success', 467, '2026-11-17T09:22:00+08:00', '定时触发', '简报归档'],
  ['run-1117-007', 'wf-content', 'failed', 509, '2026-11-17T09:16:00+08:00', '事件触发', '引用完整性检查'],
  ['run-1117-006', 'wf-feedback', 'running', 338, '2026-11-17T09:12:00+08:00', '定时触发', '反馈摘要生成'],
  ['run-1117-005', 'wf-daily', 'success', 473, '2026-11-17T09:00:00+08:00', '定时触发', '日报归档'],
  ['run-1116-004', 'wf-ticket', 'success', 461, '2026-11-16T18:42:00+08:00', '事件触发', '队列分派完成'],
  ['run-1116-003', 'wf-contract', 'success', 425, '2026-11-16T17:56:00+08:00', '手动触发', '人工复核交接'],
  ['run-1116-002', 'wf-feedback', 'failed', 640, '2026-11-16T16:24:00+08:00', '定时触发', '反馈主题聚类'],
  ['run-1116-001', 'wf-content', 'cancelled', 97, '2026-11-16T15:08:00+08:00', '手动触发', '操作人取消'],
];
export const demoRuns: Run[] = runRows.map(([id, workflowId, status, duration, startedAt, trigger, node]) => {
  const workflow = demoWorkflows.find((item) => item.id === workflowId)!;
  return {
    id,
    workflowId,
    name: workflow.name,
    bot: workflow.bot,
    status,
    duration,
    startedAt,
    trigger,
    node,
  };
});
