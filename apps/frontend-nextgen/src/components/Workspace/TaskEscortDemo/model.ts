/** Isolated UI fixtures. No runtime, API, deployment or production data is used. */
export const WORKFLOW_ID = 'onemodel-risk-defense';
export const WORKFLOW_NAME = 'OneModel 攻防工作流';
export type NodeId = 'start' | 'risk' | 'strategy' | 'approval' | 'summary';
export type RunStatus =
  | 'running'
  | 'waiting'
  | 'blocked'
  | 'queued'
  | 'success'
  | 'failed'
  | 'terminated'
  | 'cancelled';
export type DemoRange = '24h' | '7d';
export type EditorView = 'canvas' | 'yaml';
export interface DemoNode {
  id: NodeId;
  name: string;
  kind: string;
  description: string;
  timeoutSeconds: number;
}
export interface DemoRun {
  id: string;
  status: RunStatus;
  node: NodeId;
  version: string;
  startedAt: string;
  ageHours: number;
  duration: string;
}
export interface DemoWorkflow {
  id: string;
  name: string;
  version: string;
  canView: boolean;
  nodes: DemoNode[];
  runs: DemoRun[];
  nodeLatencies: Record<NodeId, number[]>;
  nodeStatuses: Record<NodeId, RunStatus>;
}
export const STATUS_LABEL: Record<RunStatus, string> = {
  running: '运行中',
  waiting: '等待中',
  blocked: '阻塞',
  queued: '排队中',
  success: '成功',
  failed: '失败',
  terminated: '终止',
  cancelled: '取消',
};
export const INITIAL_NODES: DemoNode[] = [
  {
    id: 'start',
    name: '开始',
    kind: 'trigger',
    description: '接收一份虚构的风险样本，开启示意编排。',
    timeoutSeconds: 30,
  },
  {
    id: 'risk',
    name: '风险识别',
    kind: 'analysis',
    description: '识别演示样本中的风险信号，生成结构化标签。',
    timeoutSeconds: 60,
  },
  {
    id: 'strategy',
    name: '策略评估',
    kind: 'condition',
    description: '对照演示策略进行评估，高风险进入人工审批。',
    timeoutSeconds: 120,
  },
  {
    id: 'approval',
    name: '人工审批',
    kind: 'human',
    description: '等待模拟审批意见；不会向任何人发送通知。',
    timeoutSeconds: 300,
  },
  {
    id: 'summary',
    name: '结果汇总',
    kind: 'output',
    description: '汇总模拟结论，仅展示结果，不写入外部系统。',
    timeoutSeconds: 30,
  },
];
export const NODE_STATUS: Record<NodeId, RunStatus> = {
  start: 'success',
  risk: 'success',
  strategy: 'running',
  approval: 'waiting',
  summary: 'queued',
};
const run = (
  suffix: string,
  status: RunStatus,
  node: NodeId,
  startedAt: string,
  ageHours: number,
  duration: string,
): DemoRun => ({
  id: `demo-run-${suffix}`,
  status,
  node,
  startedAt,
  ageHours,
  duration,
  version: 'v0.8.2-demo',
});
export const DEMO_RUNS: DemoRun[] = [
  run('1012', 'running', 'strategy', '11-18 14:28:12', 0.03, '1m 48s'),
  run('1011', 'waiting', 'approval', '11-18 14:24:08', 0.1, '5m 52s'),
  run('1010', 'blocked', 'strategy', '11-18 14:19:20', 0.18, '10m 40s'),
  run('1009', 'queued', 'start', '11-18 14:17:00', 0.22, '—'),
  run('1008', 'success', 'summary', '11-18 13:52:10', 0.63, '2m 16s'),
  run('1007', 'success', 'summary', '11-18 12:36:05', 1.9, '1m 42s'),
  run('1006', 'failed', 'risk', '11-18 11:05:30', 3.4, '38s'),
  run('1005', 'success', 'summary', '11-18 09:10:16', 5.3, '2m 08s'),
  run('1004', 'success', 'summary', '11-18 07:12:09', 7.3, '1m 56s'),
  run('1003', 'cancelled', 'approval', '11-17 06:20:00', 32.2, '3m 10s'),
  run('1002', 'terminated', 'strategy', '11-16 10:18:00', 52.2, '1m 20s'),
  run('1001', 'success', 'summary', '11-14 15:09:00', 95.3, '2m 24s'),
];
export const ABNORMAL_STATUSES: RunStatus[] = ['failed', 'terminated', 'cancelled'];
export const TERMINAL_STATUSES: RunStatus[] = ['success', ...ABNORMAL_STATUSES];
/** Separate synthetic node invocation samples, never workflow wall-clock durations. */
export const NODE_LATENCIES: Record<NodeId, number[]> = {
  start: [80, 96, 105, 120, 132],
  risk: [880, 1120, 1480, 1820, 2160],
  strategy: [920, 1310, 1640, 2080, 2640],
  approval: [110, 124, 138, 150, 172],
  summary: [210, 280, 330, 420, 560],
};
export function p95(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil(sorted.length * 0.95) - 1)] ?? 0;
}
export function getOverview(range: DemoRange, workflowRuns: DemoRun[] = DEMO_RUNS) {
  const runs = workflowRuns.filter((item) => item.ageHours <= (range === '24h' ? 24 : 168));
  const terminal = runs.filter((item) => TERMINAL_STATUSES.includes(item.status));
  const abnormal = runs.filter((item) => ABNORMAL_STATUSES.includes(item.status)).length;
  const success = terminal.filter((item) => item.status === 'success').length;
  return { runs, abnormal, successRate: terminal.length ? `${((success / terminal.length) * 100).toFixed(1)}%` : '—' };
}
