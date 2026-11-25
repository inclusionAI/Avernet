import { readOnlyJson } from './read-only-json';
import type { FanoutPage } from '../../server/contracts/run-subworkflows';
export type { FanoutPage, FanoutChildView } from '../../server/contracts/run-subworkflows';
export type SubworkflowGroupRef = { nodeId: string; groupExecutionId: string };
export type ChildRunDetail = {
  run: { flow_id: string; workflow_title: string | null; workflow_id: string; status: string; node_count: number };
  nodes: { node_id: string; node_title?: string | null; status: string; error_text?: string | null }[];
};
export function readSubworkflows(flowId: string, nodeId: string, groupExecutionId: string, signal: AbortSignal, cursor?: string) {
  const query = new URLSearchParams({ groupExecutionId, limit: '30' });
  if (cursor) query.set('cursor', cursor);
  return readOnlyJson<FanoutPage>(`/api/runs/${encodeURIComponent(flowId)}/nodes/${encodeURIComponent(nodeId)}/subworkflows?${query}`, signal);
}
export function readChildRun(flowId: string, signal: AbortSignal) {
  return readOnlyJson<ChildRunDetail>(`/api/runs/${encodeURIComponent(flowId)}`, signal);
}
export function observationError(error: unknown): string {
  const status = typeof error === 'object' && error !== null && 'status' in error ? Number(error.status) : 0;
  if (status === 401) return '需要登录后查看子运行。';
  if (status === 403) return '无权查看此运行，权限可能已变化。';
  if (status === 404) return '暂无持久化数据，或运行已不存在。';
  if (status === 409) return '执行代次或快照已变化，请刷新父运行后重试。';
  return '读取失败，请重试；不会启动、恢复或取消任何运行。';
}
