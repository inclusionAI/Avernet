import type { WorkflowVersionSource } from '../../server/contracts/workflow-version-source';
export function workflowVersionSourceLabel(source?: WorkflowVersionSource | null): string {
  if (source?.kind === 'auto_optimization') return '自动优化 · 人工确认发布';
  if (source?.kind === 'manual_optimization') return '手动优化 · 人工确认发布';
  if (source?.kind === 'rollback') return `回滚${source.restoredFromDeployNumber != null
    ? `至 #${source.restoredFromDeployNumber} · ${workflowVersionSourceLabel(source.restoredSource)}` : ''}`;
  return '来源未知';
}
