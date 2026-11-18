/** Pure, lossless workflow authoring validation. No API, stores or UI imports. */
import { dump, JSON_SCHEMA } from 'js-yaml';
import { parseLosslessAuthoringText } from './workflowAuthoringNumbers';
import type { WorkflowAuthoringSpec } from './workflowAuthoringTypes';

export function authoringRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('工作流响应格式无效');
  return value as Record<string, unknown>;
}
export function authoringText(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value.trim() || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value))
    throw new Error(`${label}无效`);
  return value;
}
export function authoringWorkflowId(value: unknown): string {
  const id = authoringText(value, '工作流 ID');
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(id) || id.includes('..') || id.endsWith('.') || id.endsWith('.lock') || /^__.+__$/.test(id))
    throw new Error('工作流 ID 无效');
  return id;
}
export function authoringJson(value: unknown): void {
  let visits = 0;
  const path = new Set<object>();
  function visit(item: unknown, depth: number): void {
    if (++visits > 100_000 || depth > 80) throw new Error('工作流结构超出安全限制');
    if (item === null || typeof item === 'boolean' || typeof item === 'string') return;
    if (typeof item === 'number' && Number.isFinite(item) && !Object.is(item, -0) && (!Number.isInteger(item) || Number.isSafeInteger(item))) return;
    if (!item || typeof item !== 'object' || (!Array.isArray(item) && Object.getPrototypeOf(item) !== Object.prototype))
      throw new Error('工作流包含非 JSON 数据');
    if (path.has(item)) throw new Error('工作流包含循环引用');
    path.add(item);
    const keys = Reflect.ownKeys(item);
    if (keys.some(key => typeof key !== 'string') || Array.isArray(item) && keys.length !== item.length + 1) throw new Error('工作流包含非 JSON 字段');
    if (Array.isArray(item)) for (let index = 0; index < item.length; index++) {
      if (!Object.prototype.hasOwnProperty.call(item, String(index))) throw new Error('工作流数组存在空项');
    }
    for (const key of keys) {
      if (Array.isArray(item) && key === 'length') continue;
      const descriptor = Object.getOwnPropertyDescriptor(item, key);
      if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) throw new Error('工作流包含非 JSON 字段');
      visit(descriptor.value, depth + 1);
    }
    path.delete(item);
  }
  visit(value, 0);
}
export function authoringSpec(value: unknown, expectedId?: string): WorkflowAuthoringSpec {
  authoringJson(value);
  const row = authoringRecord(value);
  const id = authoringWorkflowId(row.id);
  if (expectedId !== undefined && id !== expectedId) throw new Error('规格不属于当前工作流');
  authoringText(row.title, '工作流名称');
  if (row.version !== undefined && typeof row.version !== 'string' &&
    !(typeof row.version === 'number' && Number.isFinite(row.version) && row.version >= 0)) throw new Error('工作流版本无效');
  if (!Array.isArray(row.nodes) || !row.nodes.length) throw new Error('工作流至少需要一个节点');
  const ids = new Set<string>();
  for (const raw of row.nodes) {
    const node = authoringRecord(raw), executor = authoringRecord(node.executor);
    const nodeId = authoringText(node.id, '节点 ID');
    if (ids.has(nodeId)) throw new Error('节点 ID 重复');
    ids.add(nodeId);
    authoringText(executor.type, 'executor.type');
    if (node.dependsOn !== undefined && (!Array.isArray(node.dependsOn) ||
      node.dependsOn.some(dep => typeof dep !== 'string' || !dep.trim()))) throw new Error('节点依赖格式无效');
  }
  const nodes = row.nodes as WorkflowAuthoringSpec['nodes'];
  const incoming = new Map(nodes.map(node => [node.id, new Set(node.dependsOn ?? []).size]));
  const outgoing = new Map<string, string[]>();
  for (const node of nodes) {
    for (const dep of new Set(node.dependsOn ?? [])) {
      if (!ids.has(dep)) throw new Error('节点依赖不存在');
      outgoing.set(dep, [...(outgoing.get(dep) ?? []), node.id]);
    }
    // Object-form runtime branch extensions are deliberately left to the server.
    if (Array.isArray(node.onResult)) for (const branch of node.onResult) {
      if (!ids.has(authoringText(authoringRecord(branch).target, '结果分支目标'))) throw new Error('结果分支目标不存在');
    }
  }
  const queue = nodes.filter(node => incoming.get(node.id) === 0).map(node => node.id);
  for (let index = 0; index < queue.length; index++) for (const target of outgoing.get(queue[index]) ?? []) {
    incoming.set(target, incoming.get(target)! - 1);
    if (incoming.get(target) === 0) queue.push(target);
  }
  if (queue.length !== nodes.length) throw new Error('工作流依赖存在环');
  return JSON.parse(JSON.stringify(row)) as WorkflowAuthoringSpec;
}
export function authoringYamlText(value: unknown): string {
  const text = authoringText(value, 'YAML');
  if (new TextEncoder().encode(text).length > 100_000) throw new Error('YAML 超过 100000 字节限制');
  return text;
}
export function parseWorkflowAuthoringYaml(yaml: string): WorkflowAuthoringSpec {
  const parsed: unknown = parseLosslessAuthoringText(authoringYamlText(yaml));
  const row = authoringRecord(parsed);
  // Smart onboarding may emit a workflow metadata wrapper. Preserve it and all
  // extension keys while projecting the documented id/title/version to the root.
  if (row.id === undefined && row.workflow && typeof row.workflow === 'object') {
    const meta = authoringRecord(row.workflow);
    return authoringSpec({ ...meta, ...row, id: meta.id, title: meta.title ?? meta.id, version: meta.version ?? '1' });
  }
  return authoringSpec(row);
}
export function workflowAuthoringYaml(spec: WorkflowAuthoringSpec): string {
  return dump(authoringSpec(spec), { noRefs: true, lineWidth: -1, sortKeys: false, schema: JSON_SCHEMA });
}
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]));
  return value;
}
/** Includes layout; equivalent YAML formatting does not make a draft stale. */
export function workflowAuthoringSignature(spec: WorkflowAuthoringSpec): string {
  return JSON.stringify(canonical(authoringSpec(spec)));
}
export function restoreWorkflowAuthoringLayout(candidate: WorkflowAuthoringSpec, base: WorkflowAuthoringSpec): WorkflowAuthoringSpec {
  const oldNodes = new Map(base.nodes.map(node => [node.id, node]));
  return { ...candidate, nodes: candidate.nodes.map(node => {
    const content = { ...node };
    delete content._x;
    delete content._y;
    const old = oldNodes.get(node.id);
    return { ...content, ...(old?._x === undefined ? {} : { _x: old._x }), ...(old?._y === undefined ? {} : { _y: old._y }) };
  }) };
}
export function assertWorkflowAuthoringBaseline(candidate: WorkflowAuthoringSpec, base: WorkflowAuthoringSpec): void {
  if (candidate.id !== base.id) throw new Error('AI 不得修改当前工作流 ID');
  if (String(candidate.version) !== String(base.version)) throw new Error('AI 不得修改当前工作流版本');
}
export function buildBlankWorkflowAuthoringSpec(workflowId: string, title: string): WorkflowAuthoringSpec {
  const id = authoringWorkflowId(workflowId);
  return { id, title: authoringText(title, '工作流名称'), version: '1.0.0',
    nodes: [{ id: `${id}_start`, title: '开始', executor: { type: 'done' } }] };
}
