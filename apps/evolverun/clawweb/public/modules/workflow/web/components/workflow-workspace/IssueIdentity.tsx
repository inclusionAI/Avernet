const modes: Record<string, string> = {
  timeout: '执行超时', 'token-waste': 'Token 消耗异常', 'output-contract': '输出不符合约定',
  'repetitive-retry': '重复重试', 'prompt-ambiguity': '提示词歧义',
}

export default function IssueIdentity({ node, mode }: { node: string; mode: string }) {
  return <>
    <span className="text-sm text-slate-600">节点 <span className="ml-1 text-base font-semibold text-slate-950">{node || '工作流整体'}</span></span>
    <span className="inline-flex items-center gap-1.5 rounded-md bg-slate-100 px-2.5 py-1 text-sm text-slate-700">
      <span className="text-xs text-slate-500">问题类型</span><strong className="font-medium" title={mode}>{modes[mode] ?? mode}</strong>
    </span>
  </>
}
