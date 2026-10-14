import type { ReactNode } from 'react'

/** The first two stages share one workspace; later stages require real publication capabilities. */
export default function RepairWorkspace({ stage, title, subtitle, onBack, children }: {
  stage: 1 | 2; title: string; subtitle: string; onBack: () => void; children: ReactNode
}) {
  return <section aria-label="Workflow 修复工作区" className="mx-auto max-w-[96rem] space-y-6 pb-8">
    <button type="button" onClick={onBack} className="text-xs text-muted-foreground hover:text-primary">← 返回问题与优化</button>
    <header><p className="mb-2 font-mono text-[10px] font-semibold tracking-[0.2em] text-primary">WORKFLOW REPAIR · ONE TASK, ONE CHANGESET</p>
      <h2 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h2>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">{subtitle}</p></header>
    <ol aria-label="修复步骤" className="grid grid-cols-2 overflow-hidden rounded-lg border border-input bg-card lg:grid-cols-4">
      {['选择建议', '审核 Pack', '发布与灰度', '效果与复发'].map((label, index) => <li key={label}
        aria-current={index + 1 === stage ? 'step' : undefined}
        className={`flex items-center gap-3 border-r border-input px-5 py-4 ${index + 1 === stage ? 'border-b-2 border-b-primary bg-primary/10 text-primary' : index > 1 ? 'text-muted-foreground/70' : 'text-muted-foreground'}`}>
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs ${index + 1 === stage ? 'border-primary bg-primary text-primary-foreground' : 'border-input'}`}>{index + 1}</span>
        <div><p className="text-xs font-semibold">{label}</p><p className="mt-1 text-[10px]">{['确定本次范围', 'Diff / 反馈继续优化', '当前未接入发布', '发布后逐项验证'][index]}</p></div>
      </li>)}
    </ol>
    {children}
  </section>
}
