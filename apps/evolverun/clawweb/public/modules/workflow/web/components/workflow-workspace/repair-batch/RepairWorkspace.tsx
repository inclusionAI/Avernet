import type { ReactNode } from 'react'

/** The first two stages share one workspace; later stages require real publication capabilities. */
export default function RepairWorkspace({ stage, title, subtitle, onBack, backLabel = '返回问题与优化', onSelect, onReview, busy = false, children }: {
  stage: 1 | 2; title: string; subtitle: string; onBack: () => void; backLabel?: string; onSelect?: () => void; onReview?: () => void; busy?: boolean; children: ReactNode
}) {
  return <section aria-label="Workflow 修复工作区" className="mx-auto max-w-[96rem] space-y-6 pb-8">
    <button type="button" disabled={busy} onClick={onBack} className="text-xs text-muted-foreground enabled:cursor-pointer hover:text-primary disabled:cursor-not-allowed">← {backLabel}</button>
    <header><p className="mb-2 font-mono text-[10px] font-semibold tracking-[0.2em] text-primary">WORKFLOW REPAIR · ONE TASK, ONE CHANGESET</p>
      <h2 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h2>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">{subtitle}</p></header>
    <ol aria-label="修复步骤" className="grid grid-cols-2 overflow-hidden rounded-lg border border-input bg-card lg:grid-cols-4">
      {['选择建议', '审核 Pack', '发布与灰度', '效果与复发'].map((label, index) => <li key={label}
        aria-current={index + 1 === stage ? 'step' : undefined}
        className={`border-r border-input ${index + 1 === stage ? 'border-b-2 border-b-primary bg-primary/10 text-primary' : index > 1 ? 'text-muted-foreground/70' : 'text-muted-foreground'}`}>
        <button type="button" aria-label={label} disabled={busy || index > 1 || (index + 1 !== stage && !(index === 0 ? onSelect : onReview))}
          onClick={index === 0 ? onSelect : onReview}
          className="flex h-full w-full items-center gap-3 px-5 py-4 text-left enabled:cursor-pointer enabled:hover:bg-primary/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-not-allowed">
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs ${index + 1 === stage ? 'border-primary bg-primary text-primary-foreground' : 'border-input'}`}>{index + 1}</span>
        <div><p className="text-xs font-semibold">{label}</p><p className="mt-1 text-[10px]">{['确定本次范围', onReview || stage === 2 ? 'Diff / 反馈继续优化' : '生成候选后可审核', '未开放 · 尚未接入发布', '未开放 · 发布后验证'][index]}</p></div>
        </button>
      </li>)}
    </ol>
    {children}
  </section>
}
