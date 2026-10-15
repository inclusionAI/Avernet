import type { ReactNode } from 'react'

/** Navigation follows durable review/publication state, not a decorative roadmap. */
export default function RepairWorkspace({ stage, title, subtitle, onBack, backLabel = '返回问题与优化', onSelect, onReview, onDeploy, onVerify, busy = false, children }: {
  stage: 1 | 2 | 3 | 4; title: string; subtitle: string; onBack: () => void; backLabel?: string; onSelect?: () => void; onReview?: () => void; onDeploy?: () => void; onVerify?: () => void; busy?: boolean; children: ReactNode
}) {
  const handlers = [onSelect, onReview, onDeploy, onVerify]
  return <section aria-label="Workflow 修复工作区" className="mx-auto max-w-[96rem] space-y-6 pb-8">
    <button type="button" disabled={busy} onClick={onBack} className="text-xs text-muted-foreground enabled:cursor-pointer hover:text-primary disabled:cursor-not-allowed">← {backLabel}</button>
    <header><h2 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h2>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">{subtitle}</p></header>
    <ol aria-label="修复步骤" className="grid grid-cols-2 overflow-hidden rounded-lg border border-input bg-card lg:grid-cols-4">
      {['选择建议', '审核 Pack', '部署候选', '效果验证'].map((label, index) => <li key={label}
        aria-current={index + 1 === stage ? 'step' : undefined}
        className={`border-r border-input ${index + 1 === stage ? 'border-b-2 border-b-primary bg-primary/10 text-primary' : index > 1 ? 'text-muted-foreground/70' : 'text-muted-foreground'}`}>
        <button type="button" aria-label={label} disabled={busy || !handlers[index] && index + 1 !== stage}
          onClick={handlers[index]}
          className="flex h-full w-full items-center gap-3 px-5 py-4 text-left enabled:cursor-pointer enabled:hover:bg-primary/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-not-allowed">
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs ${index + 1 === stage ? 'border-primary bg-primary text-primary-foreground' : 'border-input'}`}>{index + 1}</span>
        <div><p className="text-xs font-semibold">{label}</p><p className="mt-1 text-[10px]">{['确定本次范围', onReview || stage === 2 ? 'Diff / 检查 / 确认候选' : '生成候选后可审核', '确认候选后明确部署', '新版本运行与逐项结论'][index]}</p></div>
        </button>
      </li>)}
    </ol>
    {children}
  </section>
}
