import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import { hasResultBranches } from './contracts';
import { branchEditError, canConnect, dependencies, findNodeReferences, graphMutationError } from './graph';
export function connectNodes(spec: EditorSpec, source: string, target: string, branchId?: string): EditorSpec {
  const error = canConnect(spec, source, target, branchId);
  if (error) throw new Error(error);
  return {
    ...spec,
    nodes: spec.nodes.map((node) =>
      node.id === target
        ? { ...node, dependsOn: [...dependencies(node), source], ...(branchId === undefined ? {} : { branchId }) }
        : node,
    ),
  };
}
export function removeConnection(spec: EditorSpec, source: string, target: string): EditorSpec {
  const next = {
    ...spec,
    nodes: spec.nodes.map((node) =>
      node.id === target ? { ...node, dependsOn: dependencies(node).filter((id) => id !== source) } : node,
    ),
  };
  const error = graphMutationError(spec, next);
  if (error) throw new Error(error);
  return next;
}
export function deleteNode(spec: EditorSpec, id: string): EditorSpec {
  const refs = findNodeReferences(spec, id);
  if (refs.length) throw new Error(`节点仍被引用: ${refs.join(', ')}`);
  const next = {
    ...spec,
    nodes: spec.nodes
      .filter((node) => node.id !== id)
      .map((node) =>
        dependencies(node).includes(id) ? { ...node, dependsOn: dependencies(node).filter((dep) => dep !== id) } : node,
      ),
  };
  const error = branchEditError(spec, next) ?? graphMutationError(spec, next);
  if (error) throw new Error(error);
  return next;
}

/** Copy without renaming arbitrary extension references. Branch origins cannot be
 * duplicated safely without a schema-aware rewrite, so reject them explicitly. */
export function duplicateNode(spec: EditorSpec, id: string): EditorSpec {
  const original = spec.nodes.find((node) => node.id === id);
  if (!original) throw new Error('节点不存在 (missing node)');
  if (hasResultBranches(original)) throw new Error('包含结果分支的节点不能直接复制，请先配置独立分支');
  const copy = structuredClone(original);
  copy.id = `node-${globalThis.crypto.randomUUID()}`;
  copy.title = `${original.title || original.id} 副本`;
  const next = { ...spec, nodes: [...spec.nodes, copy] };
  const error = branchEditError(spec, next) ?? graphMutationError(spec, next);
  if (error) throw new Error(error);
  return next;
}
