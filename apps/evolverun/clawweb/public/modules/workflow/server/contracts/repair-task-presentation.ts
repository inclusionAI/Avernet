import type { RepairBatchInput } from './repair-batch.js';

/** Task identity comes from the original frozen scope, independent of the viewed revision. */
export function repairTaskPresentation(input: RepairBatchInput) {
  const source = input.automation ? 'automatic' as const : 'manual' as const;
  const count = input.automation?.totalItems ?? input.items.length;
  if (input.automation?.batchCount && input.automation.batchCount > 1 && input.automation.totalItems === undefined) return {title:`工作流优化（分 ${input.automation.batchCount} 批）`, source};
  const summary = input.items.length === 1 && count === 1 ? input.items[0].proposal?.summary : null;
  if (typeof summary === 'string') {
    const title = summary.trim().split('\n')[0].trim();
    if (title && title.length <= 72 && !/^(当前生效 Pack 已变化|原修改在生效版本明确复现)/.test(title)) return { title, source };
  }
  const nodes = [...new Set(input.items.flatMap(item => [
    item.context?.nodeId,
    ...(Array.isArray(item.proposal?.operations) ? item.proposal.operations.map(operation => operation && typeof operation === 'object' ? operation.nodeId : undefined) : []),
  ]).filter((node): node is string => typeof node === 'string' && !!node.trim()).map(node => node.trim()))];
  const names = nodes.slice(0, 2).map(node => node.length > 24 ? `${node.slice(0, 24)}…` : node).join('、');
  const incomplete = nodes.length > 2 || count > input.items.length;
  return { title: names ? `优化 ${names}${incomplete ? ' 等节点' : ' 节点'}（${count} 条建议）` : `工作流优化（${count} 条建议）`, source };
}
