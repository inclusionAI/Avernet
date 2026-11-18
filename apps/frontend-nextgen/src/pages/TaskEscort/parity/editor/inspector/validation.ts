import type { EditorNode } from '@/domain/taskEscortParity/editorTypes';
import { authoringJson, authoringRecord, authoringSpec } from '@/domain/taskEscortParity/workflowAuthoring';
import { parseLosslessAuthoringText } from '@/domain/taskEscortParity/workflowAuthoringNumbers';
import { branchEditError, graphMutationError } from '../canvas/graph';

export function parseInspectorJson(text: string): unknown {
  const value: unknown = JSON.parse(text);
  authoringJson(value);
  // js-yaml treats JSON exponent-only spellings as strings unless a decimal
  // point is present. Normalize number tokens only, never quoted strings.
  const guarded = text.replace(/"(?:\\.|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g, (token) =>
    !token.startsWith('"') && /[eE]/.test(token) && !token.includes('.') ? token.replace(/[eE]/, '.0e') : token,
  );
  parseLosslessAuthoringText(guarded);
  const inspect = (item: unknown): void => {
    if (!item || typeof item !== 'object') return;
    for (const [key, child] of Object.entries(item)) {
      if (['__proto__', 'prototype', 'constructor'].includes(key)) throw new Error('JSON 包含不安全字段');
      inspect(child);
    }
  };
  inspect(value);
  return value;
}
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
function branches(node: EditorNode): Record<string, unknown>[] {
  const routing = node.onResult;
  if (!routing || typeof routing !== 'object' || Array.isArray(routing)) return [];
  const rows = (routing as Record<string, unknown>).branches;
  return Array.isArray(rows) ? rows.map(authoringRecord) : [];
}
/** Omission is not a deletion UI: preserve every existing extension key. */
function preserveKeys(before: unknown, after: unknown, path = ''): void {
  if (!before || typeof before !== 'object' || Array.isArray(before)) return;
  if (!after || typeof after !== 'object' || Array.isArray(after)) throw new Error(`${path} 原始对象字段必须保留`);
  for (const [key, value] of Object.entries(before)) {
    if (!Object.prototype.hasOwnProperty.call(after, key)) throw new Error(`${path}${key} 原始字段不能静默删除`);
    preserveKeys(value, (after as Record<string, unknown>)[key], `${path}${key}.`);
  }
}
export function validateNode(before: EditorNode, value: unknown, nodes?: EditorNode[]): EditorNode {
  authoringJson(value);
  const row = authoringRecord(value);
  const executor = authoringRecord(row.executor);
  if (row.id !== before.id || executor.type !== before.executor.type || row.type !== before.type)
    throw new Error('节点 id / type 与 executor.type 只读');
  if (row._x !== before._x || row._y !== before._y) throw new Error('画布坐标只读');
  if (row.title !== undefined && typeof row.title !== 'string') throw new Error('节点名称必须是字符串');
  preserveKeys(before, row);
  if (
    !same(before.triggerRule, row.triggerRule) &&
    !['all_success', 'one_success', 'all_done'].includes(String(row.triggerRule))
  )
    throw new Error('触发规则无效');
  if (!same(before.executor.outputMode, executor.outputMode) && !['json', 'text'].includes(String(executor.outputMode)))
    throw new Error('输出形式无效');
  if (
    !same(before.executor.approvalPolicy, executor.approvalPolicy) &&
    !['any', 'all', 'majority'].includes(String(executor.approvalPolicy))
  )
    throw new Error('审批通过规则无效');
  const next = row as EditorNode;
  for (const key of ['prompt', 'command', 'tool', 'server', 'skillName', 'message']) {
    if (!same(before.executor[key], executor[key]) && typeof executor[key] !== 'string')
      throw new Error(`${key} 必须是字符串`);
  }
  for (const key of ['timeoutMs', 'timeoutSeconds'])
    if (
      !same(before.executor[key], executor[key]) &&
      (typeof executor[key] !== 'number' || (executor[key] as number) < 0)
    )
      throw new Error(`${key} 必须是非负数字`);
  if (
    !same(before.dependsOn, next.dependsOn) ||
    !same(before.onResult, next.onResult) ||
    !same(before.branchId, next.branchId)
  ) {
    if (!nodes) throw new Error('需要完整 nodes 才能验证依赖与分支修改');
    if (next.dependsOn && new Set(next.dependsOn).size !== next.dependsOn.length) throw new Error('依赖不能重复');
    const rows = branches(next);
    for (const old of branches(before))
      if (
        nodes.some((n) => n.id !== before.id && n.branchId === old.branchId) &&
        !rows.some((b) => b.branchId === old.branchId)
      )
        throw new Error(`分支 ${old.branchId} 已被引用，不能删除或改标识`);
    const beforeSpec = { id: 'inspector', title: 'Inspector', nodes };
    const afterSpec = { ...beforeSpec, nodes: nodes.map((n) => (n.id === before.id ? next : n)) };
    authoringSpec(afterSpec);
    const mutationError = branchEditError(beforeSpec, afterSpec) ?? graphMutationError(beforeSpec, afterSpec);
    if (mutationError) throw new Error(mutationError);
    if (!same(before.onResult, next.onResult)) {
      const used = new Set(nodes.filter((n) => n.id !== before.id).flatMap((n) => branches(n).map((b) => b.branchId)));
      rows.forEach((branch, index) => {
        if (typeof branch.branchId !== 'string' || !branch.branchId.trim() || used.has(branch.branchId))
          throw new Error('分支标识不能为空或重复');
        used.add(branch.branchId);
        const match = authoringRecord(branch.match);
        if (Object.values(match).some((v) => v !== null && !['string', 'number', 'boolean'].includes(typeof v)))
          throw new Error('分支 match 仅支持标量');
        if (!Object.keys(match).length && index !== rows.length - 1) throw new Error('兜底匹配必须位于最后');
        if (branch.complete !== undefined && typeof branch.complete !== 'boolean')
          throw new Error('complete 必须是布尔值');
      });
    }
    if (
      next.branchId !== undefined &&
      !nodes.some((n) => branches(n.id === next.id ? next : n).some((b) => b.branchId === next.branchId))
    )
      throw new Error('分支归属不存在');
  }
  return next;
}
