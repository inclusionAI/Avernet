import {
  DEMO_RUNS,
  DemoNode,
  DemoRun,
  DemoWorkflow,
  INITIAL_NODES,
  NODE_LATENCIES,
  NODE_STATUS,
  NodeId,
  RunStatus,
  WORKFLOW_ID,
  WORKFLOW_NAME,
} from './model';

const inspectionNodes: DemoNode[] = [
  {
    id: 'start',
    name: '开始',
    kind: 'trigger',
    description: '接收虚构的每日巡检任务，不连接真实指标系统。',
    timeoutSeconds: 30,
  },
  {
    id: 'risk',
    name: '采集指标',
    kind: 'collection',
    description: '读取预置的模拟指标样本，不发起任何数据请求。',
    timeoutSeconds: 120,
  },
  {
    id: 'strategy',
    name: '异常分析',
    kind: 'analysis',
    description: '对模拟指标进行阈值检查，整理虚构的异常线索。',
    timeoutSeconds: 90,
  },
  {
    id: 'approval',
    name: '人工复核',
    kind: 'human',
    description: '展示模拟复核状态，不通知真实值班人员。',
    timeoutSeconds: 600,
  },
  {
    id: 'summary',
    name: '生成报告',
    kind: 'output',
    description: '生成示意巡检结论，不向外部系统写入报告。',
    timeoutSeconds: 60,
  },
];
const inspectionRun = (
  suffix: string,
  status: RunStatus,
  node: NodeId,
  startedAt: string,
  ageHours: number,
  duration: string,
): DemoRun => ({
  id: `demo-inspection-${suffix}`,
  status,
  node,
  startedAt,
  ageHours,
  duration,
  version: 'v1.2.0-demo',
});
const reportNodes: DemoNode[] = [
  {
    id: 'start',
    name: '开始',
    kind: 'trigger',
    description: '演示报告汇总入口，尚无模拟运行记录。',
    timeoutSeconds: 30,
  },
  {
    id: 'risk',
    name: '读取巡检记录',
    kind: 'collection',
    description: '示意读取本地虚构巡检记录，不访问外部数据。',
    timeoutSeconds: 60,
  },
  {
    id: 'strategy',
    name: '汇总分类',
    kind: 'analysis',
    description: '按模拟异常类别整理摘要，不执行真实分析。',
    timeoutSeconds: 120,
  },
  {
    id: 'approval',
    name: '审核摘要',
    kind: 'human',
    description: '示意摘要审核步骤，不发送真实审核请求。',
    timeoutSeconds: 300,
  },
  {
    id: 'summary',
    name: '输出报告',
    kind: 'output',
    description: '示意报告输出步骤，不保存任何外部文件。',
    timeoutSeconds: 45,
  },
];
const emptyLatencies: Record<NodeId, number[]> = {
  start: [],
  risk: [],
  strategy: [],
  approval: [],
  summary: [],
};
// With no runs, queued denotes only the schematic topology, not a live execution.
const idleStatuses: Record<NodeId, RunStatus> = {
  start: 'queued',
  risk: 'queued',
  strategy: 'queued',
  approval: 'queued',
  summary: 'queued',
};

/** canView is mock permission data for this demo, never an actual authorization decision. */
export const DEMO_WORKFLOWS: readonly DemoWorkflow[] = [
  {
    id: WORKFLOW_ID,
    name: WORKFLOW_NAME,
    version: 'v0.8.2-demo',
    canView: true,
    nodes: INITIAL_NODES,
    runs: DEMO_RUNS,
    nodeLatencies: NODE_LATENCIES,
    nodeStatuses: NODE_STATUS,
  },
  {
    id: 'daily-metrics-inspection',
    name: '每日指标巡检',
    version: 'v1.2.0-demo',
    canView: true,
    nodes: inspectionNodes,
    runs: [
      inspectionRun('2010', 'running', 'risk', '11-18 14:27:00', 0.05, '2m 00s'),
      inspectionRun('2009', 'blocked', 'approval', '11-18 14:12:00', 0.3, '17m 00s'),
      inspectionRun('2008', 'waiting', 'approval', '11-18 13:45:00', 0.75, '44m 00s'),
      inspectionRun('2007', 'success', 'summary', '11-18 12:10:00', 2.3, '4m 36s'),
      inspectionRun('2006', 'failed', 'strategy', '11-18 10:30:00', 4, '1m 12s'),
      inspectionRun('2005', 'success', 'summary', '11-18 08:00:00', 6.5, '3m 48s'),
      inspectionRun('2004', 'terminated', 'risk', '11-17 07:00:00', 31.5, '2m 10s'),
      inspectionRun('2003', 'cancelled', 'start', '11-15 11:00:00', 75.5, '12s'),
    ],
    nodeLatencies: {
      start: [45, 52, 60, 68, 75],
      risk: [1800, 2200, 3100, 4200, 5400],
      strategy: [350, 480, 620, 780, 960],
      approval: [180, 210, 260, 320, 390],
      summary: [600, 850, 1100, 1420, 1880],
    },
    nodeStatuses: {
      start: 'success',
      risk: 'success',
      strategy: 'success',
      approval: 'blocked',
      summary: 'queued',
    },
  },
  {
    id: 'report-summary',
    name: '巡检报告汇总',
    version: 'v0.1.0-demo',
    canView: true,
    nodes: reportNodes,
    runs: [],
    nodeLatencies: emptyLatencies,
    nodeStatuses: idleStatuses,
  },
  {
    id: 'restricted-operations',
    name: '运维受限示例',
    version: 'v0.1.0-demo',
    canView: false,
    nodes: INITIAL_NODES.map((node) => ({ ...node })),
    runs: [],
    nodeLatencies: emptyLatencies,
    nodeStatuses: idleStatuses,
  },
];

/** Filter the synthetic catalog only; real authorization is outside this demo. */
export function getAccessibleDemoWorkflows(catalog: readonly DemoWorkflow[]): DemoWorkflow[] {
  return catalog.filter((workflow) => workflow.canView);
}
