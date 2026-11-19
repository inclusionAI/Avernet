import { dump, load } from 'js-yaml';
import { DemoNode, DemoWorkflow, INITIAL_NODES, WORKFLOW_ID, WORKFLOW_NAME } from './model';

type WorkflowTopology = Pick<DemoWorkflow, 'id' | 'name' | 'nodes'>;
const DEFAULT_WORKFLOW: WorkflowTopology = { id: WORKFLOW_ID, name: WORKFLOW_NAME, nodes: INITIAL_NODES };

export function serializeWorkflow(nodes: DemoNode[], workflow: WorkflowTopology = DEFAULT_WORKFLOW): string {
  return dump({ demo: true, id: workflow.id, name: workflow.name, nodes }, { noRefs: true, lineWidth: 100 });
}
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function onlyKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).every((key) => keys.includes(key));
}
/** A deliberately narrow demo format: editable properties, fixed five-node topology. */
export function parseWorkflow(text: string, workflow: WorkflowTopology = DEFAULT_WORKFLOW): DemoNode[] {
  if (text.length > 24000) throw new Error('演示 YAML 不可超过 24,000 字符。');
  let value: unknown;
  try {
    value = load(text);
  } catch {
    throw new Error('YAML 语法无效，请检查缩进与字段格式。');
  }
  if (
    !record(value) ||
    !onlyKeys(value, ['demo', 'id', 'name', 'nodes']) ||
    value.demo !== true ||
    value.id !== workflow.id ||
    value.name !== workflow.name ||
    !Array.isArray(value.nodes) ||
    value.nodes.length !== workflow.nodes.length
  ) {
    throw new Error('请保留 demo: true、当前工作流的标识、名称及五个示例节点。');
  }
  const nodes: unknown[] = value.nodes;
  return workflow.nodes.map((original, index) => {
    const node = nodes[index];
    if (
      !record(node) ||
      !onlyKeys(node, ['id', 'name', 'kind', 'description', 'timeoutSeconds']) ||
      node.id !== original.id ||
      node.kind !== original.kind
    ) {
      throw new Error('示意编排的节点顺序、id 与 kind 不可更改，不支持额外字段。');
    }
    if (
      typeof node.name !== 'string' ||
      !node.name.trim() ||
      node.name.length > 32 ||
      typeof node.description !== 'string' ||
      node.description.length > 400 ||
      !Number.isInteger(node.timeoutSeconds) ||
      typeof node.timeoutSeconds !== 'number' ||
      node.timeoutSeconds < 1 ||
      node.timeoutSeconds > 3600
    ) {
      throw new Error('节点名称为 1–32 字，说明不超过 400 字，超时为 1–3600 的整数。');
    }
    return {
      id: original.id,
      kind: original.kind,
      name: node.name,
      description: node.description,
      timeoutSeconds: node.timeoutSeconds,
    };
  });
}
