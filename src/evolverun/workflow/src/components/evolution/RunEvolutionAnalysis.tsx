import type { RunEvolutionAnalysisResponse } from '../../api/client'
import type { ReactNode } from 'react'

const severityStyle: Record<string, string> = {
  critical: 'bg-red-50 text-red-700',
  high: 'bg-orange-50 text-orange-700',
  medium: 'bg-amber-50 text-amber-700',
  low: 'bg-slate-100 text-slate-600',
}

type Props = {
  analysis: RunEvolutionAnalysisResponse
  renderOptimizeLink?: (diagnosis: RunEvolutionAnalysisResponse['diagnoses'][number]) => ReactNode
  variant?: 'full' | 'evidence'
  focusDiagnosisId?: string
  focusFailureSignature?: string
}

export default function RunEvolutionAnalysis({
  analysis,
  renderOptimizeLink,
  variant = 'full',
  focusDiagnosisId,
  focusFailureSignature,
}: Props) {
  const hasAnalysisContext = analysis.facts.length > 0
    || analysis.inferences.length > 0
    || analysis.unknowns.length > 0
  const emptyDiagnosisText = hasAnalysisContext
    ? '本次分析未生成有效诊断，请重新分析。'
    : '本次分析未生成诊断。'
  const focusedDiagnosis = analysis.diagnoses.find((diagnosis) => (
    (focusDiagnosisId && diagnosis.diagnosisId === focusDiagnosisId)
    || (focusFailureSignature && diagnosis.failureSignature === focusFailureSignature)
  )) ?? analysis.diagnoses[0]

  if (variant === 'evidence') {
    return (
      <div data-variant="analysis-evidence" className="space-y-3">
        <section>
          <div className="flex items-center justify-between gap-3">
            <h4 className="text-xs font-semibold text-slate-800">问题来源</h4>
            {focusedDiagnosis && (
              <span className="text-[10px] text-slate-400">
                {focusedDiagnosis.sourceEvidence.length} 条引用
              </span>
            )}
          </div>
          {focusedDiagnosis ? (
            <EvidenceList diagnosis={focusedDiagnosis} evidenceStatus={analysis.evidenceStatus} compact />
          ) : (
            <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-500">{emptyDiagnosisText}</p>
          )}
        </section>

        <details className="rounded-lg border border-slate-200 bg-white px-3 py-2.5">
          <summary className="cursor-pointer text-xs font-semibold text-slate-700">判断依据</summary>
          <div className="mt-3 grid gap-3 text-[11px] leading-5 md:grid-cols-3">
            <Basis title="事实" items={analysis.facts} />
            <Basis title="推断" items={analysis.inferences} />
            <Basis title="待确认" items={analysis.unknowns} />
          </div>
        </details>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {analysis.diagnoses.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-200 px-4 py-5 text-xs text-slate-500">
          {emptyDiagnosisText}
        </div>
      ) : analysis.diagnoses.map((diagnosis) => (
        <article key={diagnosis.diagnosisId} className="rounded-lg border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-slate-900">{diagnosis.nodeId ?? '工作流'}</span>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">{diagnosis.failureMode}</span>
                <span className={`rounded-full px-2 py-0.5 text-[10px] ${severityStyle[diagnosis.severity] ?? severityStyle.low}`}>
                  {diagnosis.severity}
                </span>
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-700">{diagnosis.reasoning}</p>
            </div>
            {renderOptimizeLink?.(diagnosis)}
          </div>

          <div className="mt-3 rounded-md bg-blue-50 px-3 py-2.5">
            <div className="text-[11px] font-semibold text-blue-800">相关建议</div>
            <p className="mt-1 text-xs leading-5 text-blue-700">
              {typeof diagnosis.proposal?.summary === 'string'
                ? diagnosis.proposal.summary
                : '问题已识别，暂无可自动应用方案。'}
            </p>
          </div>

          <div className="mt-3">
            <div className="text-[11px] font-semibold text-slate-700">问题来源</div>
            <EvidenceList diagnosis={diagnosis} evidenceStatus={analysis.evidenceStatus} />
          </div>
        </article>
      ))}

      {(analysis.facts.length > 0 || analysis.inferences.length > 0 || analysis.unknowns.length > 0) && (
        <details className="rounded-lg border border-slate-200 bg-white px-4 py-3">
          <summary className="cursor-pointer text-xs font-semibold text-slate-700">分析依据</summary>
          <div className="mt-3 grid gap-3 text-[11px] leading-5 md:grid-cols-3">
            <Basis title="事实" items={analysis.facts} />
            <Basis title="推断" items={analysis.inferences} />
            <Basis title="待确认" items={analysis.unknowns} />
          </div>
        </details>
      )}
    </div>
  )
}

function EvidenceList({
  diagnosis,
  evidenceStatus,
  compact = false,
}: {
  diagnosis: RunEvolutionAnalysisResponse['diagnoses'][number]
  evidenceStatus: RunEvolutionAnalysisResponse['evidenceStatus']
  compact?: boolean
}) {
  return (
    <div className={`${compact ? 'mt-2' : 'mt-1.5'} divide-y divide-slate-100 overflow-hidden rounded-md border border-slate-100 bg-slate-50`}>
      {diagnosis.sourceEvidence.length === 0 ? (
        <p className="px-3 py-2 text-[11px] text-slate-500">
          {evidenceStatus === 'complete'
            ? '本次诊断未引用具体结构化运行事件。'
            : '未关联结构化运行事件；本次结论基于节点状态、Trace 或运行日志，结构化证据不完整。'}
        </p>
      ) : diagnosis.sourceEvidence.map((evidence) => (
        <div key={evidence.eventId} className="px-3 py-2 text-[11px] leading-5">
          {evidence.missing ? (
            <span className="text-amber-700">{evidence.summary}</span>
          ) : (
            <>
              <div className="flex flex-wrap gap-x-2 text-slate-500">
                <span className="font-mono text-slate-700">{evidence.eventType}</span>
                <span>{evidence.nodeId ?? 'workflow'}</span>
                <span>{new Date(evidence.occurredAtMs).toLocaleString()}</span>
              </div>
              <p className="text-slate-700">{evidence.summary}</p>
            </>
          )}
        </div>
      ))}
    </div>
  )
}

function Basis({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="font-medium text-slate-700">{title}</div>
      {items.length > 0
        ? <ul className="mt-1 list-disc space-y-1 pl-4 text-slate-600">{items.map((item) => <li key={item}>{item}</li>)}</ul>
        : <p className="mt-1 text-slate-400">无</p>}
    </div>
  )
}
