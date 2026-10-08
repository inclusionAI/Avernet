const modes: Record<string, string> = {
  timeout: '执行超时', 'token-waste': 'Token 消耗异常', 'output-contract': '输出不符合约定',
  'repetitive-retry': '重复重试', 'prompt-ambiguity': '提示词歧义',
  'tool-not-found': '工具不可用', 'output-quality': '输出质量异常', other: '其他问题',
  'dependency-down': '依赖不可用', 'param-type-mismatch': '参数类型不匹配',
  performance: '执行性能异常', 'redundant-step': '冗余步骤',
}
export const issueModeLabel = (mode: string) => modes[mode] ?? mode

export default function IssueIdentity({ node, mode }: { node: string; mode: string }) {
  return <>
    <span className="text-sm text-slate-600">节点 <span className="ml-1 text-base font-semibold text-slate-950">{node || '工作流整体'}</span></span>
    <span className="inline-flex items-center gap-1.5 rounded-md bg-slate-100 px-2.5 py-1 text-sm text-slate-700">
      <span className="text-xs text-slate-500">问题类型</span><strong className="font-medium" title={mode}>{issueModeLabel(mode)}</strong>
    </span>
  </>
}
