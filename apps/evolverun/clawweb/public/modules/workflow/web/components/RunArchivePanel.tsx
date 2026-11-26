/** Read-only evidence workbench; collection and runtime status are independent. */
import { useEffect, useId, useRef, useState } from 'react'
import { useRunArchive } from '@avernet/workflow/web/api/hooks'
import type { RunArchiveData } from '@avernet/clawweb-shared/web/types'
import { ActionButton, Badge } from './run-archive/ArchivePrimitives'
import { analysisLabel, analysisSource, failureCount, formatTime, type ProcessView, type SectionId } from './run-archive/archive-helpers'
import { AnalysisSection, ErrorDetailsSection } from './run-archive/EvidenceSections'
import OverviewSection from './run-archive/OverviewSection'
import ProcessSection from './run-archive/ProcessSection'
import SuggestedYamlSection from './run-archive/SuggestedYamlSection'

type RunArchivePanelProps = { flowId: string; runStatus?: string | null }

export default function RunArchivePanel({ flowId, runStatus }: RunArchivePanelProps) {
  const eligible = runStatus === 'failed' || runStatus === 'cancelled' || runStatus === 'timeout'
  if (!eligible) {
    return (
      <section aria-label="运行档案可用性" className="rounded-xl border border-border bg-card p-6 text-center">
        <h2 className="text-base font-semibold text-foreground">当前状态不提供运行档案</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          运行档案仅对失败（failed）、已中止（cancelled）和超时（timeout）的运行提供。
          当前运行状态：{runStatus?.trim() || '未记录'}。
        </p>
      </section>
    )
  }
  return <EligibleRunArchive flowId={flowId} runStatus={runStatus} />
}

function EligibleRunArchive({ flowId, runStatus }: RunArchivePanelProps) {
  const { data, isLoading, isError, error, refetch } = useRunArchive(flowId)
  if (isLoading) {
    return (
      <div role="status" className="space-y-3 rounded-xl border border-border bg-card p-6">
        <div aria-hidden="true" className="h-8 animate-pulse rounded bg-muted" />
        <div aria-hidden="true" className="h-32 animate-pulse rounded bg-muted" />
        <p className="text-sm text-muted-foreground">正在加载运行档案…</p>
      </div>
    )
  }
  if (!data) {
    return (
      <div role="alert" className="rounded-xl border border-destructive/20 bg-destructive/10 p-5">
        <h2 className="text-base font-semibold text-destructive">加载运行档案失败</h2>
        <p className="mt-2 text-sm text-destructive [overflow-wrap:anywhere]">
          {error?.message || '未返回档案数据，无法确认采集情况。'}
        </p>
        {typeof refetch === 'function' && (
          <div className="mt-4">
            <ActionButton onClick={() => void refetch()}>重新加载档案</ActionButton>
          </div>
        )}
      </div>
    )
  }
  return (
    <ArchiveWorkbench
      key={flowId}
      data={data}
      runStatus={runStatus}
      refreshFailed={isError}
      onRetry={() => void refetch()}
    />
  )
}

const SECTIONS: Array<{ id: SectionId; label: string; description: string }> = [
  { id: 'overview', label: '运行概览', description: '先核对运行状态与档案采集范围，再选择下一步。档案结论仅限已采集的证据。' },
  { id: 'process', label: '运行过程', description: '查看流程信息、节点执行、事件流和运行日志；筛选只影响本地展示。' },
  { id: 'errors', label: '错误详情', description: '核对失败节点、对应输入输出和错误线索；没有失败记录不代表整次运行成功。' },
  { id: 'analysis', label: 'AI 诊断', description: '展示档案中已有的分析状态与诊断。诊断属于待核对的分析结论，不替代原始证据。' },
  { id: 'yaml', label: '建议 YAML', description: '审阅真实建议内容与 Patch 操作；不会自动应用，也不会补造原始 YAML。' },
]

function ArchiveWorkbench({ data, runStatus, refreshFailed, onRetry }: {
  data: RunArchiveData
  runStatus?: string | null
  refreshFailed: boolean
  onRetry: () => void
}) {
  const [section, setSection] = useState<SectionId>('overview')
  const [processView, setProcessView] = useState<ProcessView>('info')
  const [focusRequest, setFocusRequest] = useState(0)
  const contentRef = useRef<HTMLDivElement>(null)
  const id = useId()
  useEffect(() => { if (focusRequest > 0) contentRef.current?.focus() }, [focusRequest])
  const navigate = (next: SectionId, view?: ProcessView) => {
    setSection(next)
    if (view) setProcessView(view)
    setFocusRequest(request => request + 1)
  }
  const current = SECTIONS.find(item => item.id === section)!
  const hints: Record<SectionId, string> = {
    overview: data.archive.status === 'partial' ? '部分采集' : '采集概况',
    process: `${data.nodeExecutions.length} 执行 · ${data.flowEvents.length} 事件 · ${data.runLogs.length} 日志`,
    errors: failureCount(data) ? `${failureCount(data)} 条失败线索` : '未采集到失败节点',
    analysis: analysisLabel(data.analysis),
    yaml: data.suggestedYaml?.yamlContent?.trim() ? '已提供 · 未应用' : '未提供 YAML',
  }
  return (
    <div className="min-w-0 max-w-full rounded-xl border border-border bg-card text-foreground">
      <header className="flex min-w-0 flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-lg text-primary">▤</span>
          <div className="min-w-0">
            <p className="text-sm font-semibold">运行档案</p>
            <p className="mt-1 text-xs text-muted-foreground">执行证据 · 错误线索 · 修改建议</p>
          </div>
        </div>
        <Badge className={data.archive.status === 'partial' ? 'bg-warning/10 text-warning-foreground' : 'bg-primary/10 text-primary'}>{data.archive.status === 'partial' ? '部分采集 · partial' : '采集完成 · completed'}</Badge>
      </header>
      {refreshFailed && (
        <div role="alert" className="m-4 rounded-lg bg-destructive/10 p-3 text-sm text-destructive">
          档案刷新失败，以下保留上次已加载数据。
          <button type="button" onClick={onRetry} className="ml-2 rounded underline focus-visible:ring-2 focus-visible:ring-primary">
            重试刷新
          </button>
        </div>
      )}
      {(data.archive.status === 'partial' || data.archive.errors.length > 0) && <section aria-label="档案采集提示" className="m-4 min-w-0 rounded-lg border border-warning/20 bg-warning/10 p-3 text-sm sm:mx-5">
        <h3 className="font-medium text-foreground">{data.archive.status === 'partial' ? '档案仅部分采集，部分证据可能缺失' : '档案采集存在提示'}</h3>
        {data.archive.errors.length > 0 ? <ul className="mt-2 list-inside list-disc space-y-1 text-xs leading-6 text-muted-foreground [overflow-wrap:anywhere]">{data.archive.errors.map((message, index) => <li key={index}>{message}</li>)}</ul> : <p className="mt-2 text-xs text-muted-foreground">未提供具体采集错误，请按各分区的实际数据核对。</p>}
      </section>}
      <div className="grid min-w-0 grid-cols-1 md:grid-cols-[12rem_minmax(0,1fr)]">
        <nav aria-label="运行档案分区" className="flex min-w-0 flex-wrap content-start gap-1 border-b border-border bg-muted/20 p-2 md:flex-col md:flex-nowrap md:border-b-0 md:border-r md:p-3">
          {SECTIONS.map((item, index) => <button type="button" key={item.id} aria-label={item.label} aria-current={section === item.id ? 'page' : undefined} aria-controls={`${id}-content`}
            onClick={() => setSection(item.id)}
            className={`flex min-w-0 flex-1 basis-36 items-start gap-2 rounded-lg border px-3 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary md:w-full md:flex-none md:basis-auto ${section === item.id ? 'border-primary/20 bg-primary/10 text-primary' : 'border-transparent text-muted-foreground hover:bg-muted'}`}>
            <span aria-hidden="true" className="pt-0.5 font-mono text-[10px] opacity-60">0{index + 1}</span>
            <span className="min-w-0">
              <span className="block text-sm font-medium">{item.label}</span>
              <span className="mt-1 block text-[11px] leading-5 text-muted-foreground [overflow-wrap:anywhere]">
                {hints[item.id]}
              </span>
            </span>
          </button>)}
        </nav>
        <div id={`${id}-content`} ref={contentRef} role="region" aria-labelledby={`${id}-heading`} tabIndex={-1} className="min-w-0 p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary sm:p-5 lg:p-6">
          <header className="mb-5 min-w-0">
            <p aria-hidden="true" className="mb-2 font-mono text-[10px] tracking-widest text-muted-foreground">
              RUN ARCHIVE / {String(SECTIONS.findIndex(item => item.id === section) + 1).padStart(2, '0')}
            </p>
            <h2 id={`${id}-heading`} className="text-lg font-semibold tracking-tight">{current.label}</h2>
            <p className="mt-2 text-xs leading-6 text-muted-foreground [overflow-wrap:anywhere]">
              {current.description}
            </p>
          </header>
          {section === 'overview' && <OverviewSection data={data} runStatus={runStatus} navigate={navigate} />}
          {section === 'process' && <ProcessSection data={data} view={processView} onViewChange={setProcessView} />}
          {section === 'errors' && <ErrorDetailsSection data={data} navigate={navigate} />}
          {section === 'analysis' && <AnalysisSection data={data} navigate={navigate} />}
          {section === 'yaml' && <SuggestedYamlSection data={data} navigate={navigate} />}
        </div>
      </div>
      <footer aria-label="档案元信息" className="flex min-w-0 flex-wrap gap-x-6 gap-y-2 rounded-b-xl border-t border-border bg-muted/20 px-4 py-3 text-xs leading-5 text-muted-foreground sm:px-5">
        <p className="min-w-0 max-w-full [overflow-wrap:anywhere]">archive_id: <span className="font-mono text-foreground">{data.archive.archiveId}</span></p>
        <p className="min-w-0 [overflow-wrap:anywhere]">版本：{data.archive.archiveVersion}</p>
        {data.analysis && <p className="min-w-0 [overflow-wrap:anywhere]">分析来源：{analysisSource(data.analysis.source)} · {data.analysis.source}</p>}
        <p className="min-w-0 [overflow-wrap:anywhere]">生成时间：{formatTime(data.archive.createdAt)}</p>
      </footer>
    </div>
  )
}
