import type { TaskEscortWorkspaceMode } from '@/domain/taskEscortWorkspace';

export type EditorMode = 'visual' | 'split' | 'yaml';
export type EditorNodeKind = 'trigger' | 'tool' | 'agent' | 'guardian' | 'output' | 'branch';
export interface EditorNode {
  id: string;
  name: string;
  kind: EditorNodeKind;
  description: string;
  timeout: string;
  retries: string;
  x: number;
  y: number;
}
export type EditorNodePatch = Partial<Pick<EditorNode, 'name' | 'description' | 'timeout' | 'retries'>>;
export interface EditorSnapshot {
  nodes: EditorNode[];
  selected: string;
  version: string;
  past: EditorNode[][];
  future: EditorNode[][];
  dirty: boolean;
}

export const initialEditorNodes: EditorNode[] = [
  {
    id: 'start',
    name: '任务触发',
    kind: 'trigger',
    description: '接收用户指令，初始化工作流上下文',
    timeout: '10',
    retries: '0',
    x: 250,
    y: 30,
  },
  {
    id: 'fetch',
    name: '数据采集',
    kind: 'tool',
    description: '检索任务所需数据，支持超时与退避重试',
    timeout: '60',
    retries: '3',
    x: 95,
    y: 170,
  },
  {
    id: 'analyze',
    name: '智能分析',
    kind: 'agent',
    description: '由分析 Bot 识别关键信号并形成结构化结果',
    timeout: '120',
    retries: '2',
    x: 405,
    y: 170,
  },
  {
    id: 'guard',
    name: '护航校验',
    kind: 'guardian',
    description: '校验输出契约与质量阈值，异常转入人工确认',
    timeout: '30',
    retries: '1',
    x: 250,
    y: 310,
  },
  {
    id: 'finish',
    name: '结果交付',
    kind: 'output',
    description: '汇总运行结果与证据，交付任务报告',
    timeout: '30',
    retries: '0',
    x: 250,
    y: 450,
  },
];
export const editorConnections = [
  { source: 'start', target: 'fetch', path: 'M335 110V135Q335 145 325 145H190Q180 145 180 155V170' },
  { source: 'start', target: 'analyze', path: 'M335 110V135Q335 145 345 145H480Q490 145 490 155V170' },
  { source: 'fetch', target: 'guard', path: 'M180 250V275Q180 285 190 285H325Q335 285 335 295V310' },
  { source: 'analyze', target: 'guard', path: 'M490 250V275Q490 285 480 285H345Q335 285 335 295V310' },
  { source: 'guard', target: 'finish', path: 'M335 390V450' },
];
export const editorPalette: Array<{ kind: EditorNodeKind; name: string; desc: string; nodeId: string | null }> = [
  { kind: 'trigger', name: '触发器', desc: '接收任务指令', nodeId: 'start' },
  { kind: 'agent', name: '智能体', desc: '执行推理与决策', nodeId: 'analyze' },
  { kind: 'tool', name: '工具调用', desc: '连接数据与系统', nodeId: 'fetch' },
  { kind: 'branch', name: '条件分支', desc: '当前示例未配置分支节点', nodeId: null },
  { kind: 'guardian', name: '护航校验', desc: '质量与异常控制', nodeId: 'guard' },
];
export const editorModes: Array<{ id: EditorMode; label: string }> = [
  { id: 'visual', label: '可视化' },
  { id: 'split', label: '分屏' },
  { id: 'yaml', label: 'YAML' },
];
export const copyEditorNodes = (nodes: EditorNode[]) => nodes.map((node) => ({ ...node }));
export function copyEditorSnapshot(snapshot: EditorSnapshot): EditorSnapshot {
  return {
    ...snapshot,
    nodes: copyEditorNodes(snapshot.nodes),
    past: snapshot.past.map(copyEditorNodes),
    future: snapshot.future.map(copyEditorNodes),
  };
}
export function initialEditorSnapshot(mode: TaskEscortWorkspaceMode): EditorSnapshot {
  return {
    nodes: mode === 'demo' ? copyEditorNodes(initialEditorNodes) : [],
    selected: mode === 'demo' ? 'fetch' : '',
    version: 'v2.4',
    past: [],
    future: [],
    dirty: false,
  };
}
function integerInRange(value: string, min: number, max: number) {
  const number = Number(value);
  return /^\d+$/.test(value.trim()) && Number.isSafeInteger(number) && number >= min && number <= max;
}
export function validateEditorNodes(nodes: EditorNode[]) {
  const errors: string[] = [];
  if (!nodes.length) errors.push('工作流至少需要一个节点');
  for (const node of nodes) {
    const label = node.name.trim() || node.id;
    if (!node.name.trim()) errors.push(`${node.id}：节点名称不能为空`);
    if (!integerInRange(node.timeout, 1, 3600)) errors.push(`${label}：超时必须为 1–3600 秒的整数`);
    if (!integerInRange(node.retries, 0, 10)) errors.push(`${label}：重试次数必须为 0–10 的整数`);
  }
  const configurationValid = errors.length === 0;
  const ids = new Set(nodes.map((node) => node.id));
  const connectionsValid =
    ids.size === nodes.length &&
    !ids.has('') &&
    editorConnections.every(({ source, target }) => ids.has(source) && ids.has(target));
  if (!connectionsValid) errors.push('节点关系校验未通过：连接端点缺失或节点 ID 重复');
  const guardianValid = nodes.some(({ id, kind }) => id === 'guard' && kind === 'guardian');
  if (!guardianValid) errors.push('未配置护航校验节点');
  const checks = [
    { label: '节点配置完整', valid: configurationValid },
    { label: '节点关系校验通过', valid: connectionsValid },
    { label: '护航校验节点已配置', valid: guardianValid },
  ];
  return { errors, checks, valid: checks.every((check) => check.valid) };
}
export function serializeEditorWorkflow(
  nodes: EditorNode[],
  workflow: { id: string; name: string } | undefined,
  version: string,
) {
  const quote = (value: string) => JSON.stringify(value);
  return `id: ${quote(workflow?.id ?? 'demo-workflow')}\nversion: ${quote(version)}\ntitle: ${quote(
    workflow?.name ?? '智能任务工作流',
  )}\n\nnodes:\n${nodes
    .map((node) => {
      const dependencies = editorConnections.filter(({ target }) => target === node.id).map(({ source }) => source);
      return `  - id: ${quote(node.id)}\n    title: ${quote(node.name)}\n    type: ${
        node.kind
      }\n    description: ${quote(node.description)}\n    timeout: ${quote(`${node.timeout}s`)}\n    retry: ${
        node.retries
      }\n    depends_on: ${JSON.stringify(dependencies)}`;
    })
    .join('\n\n')}`;
}
