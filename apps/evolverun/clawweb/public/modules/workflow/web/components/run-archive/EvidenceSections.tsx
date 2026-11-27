import type { RunArchiveData, RunArchiveDiagnosis } from '@avernet/clawweb-shared/web/types'
import { ActionButton, Badge, CodeBlock, Disclosure, EmptyState } from './ArchivePrimitives'
import { analysisLabel, analysisSource, failureCount, failureEntries, formatTime, text, type Navigate } from './archive-helpers'

export function ErrorDetailsSection({ data, navigate }: { data: RunArchiveData; navigate: Navigate }) {
  const failures = failureEntries(data)
  return (
    <div className="min-w-0 space-y-4">
      {!failures.length && <EmptyState
        title={failureCount(data) > 0 ? '失败节点详情未采集' : '未采集到失败节点'}
        description={failureCount(data) > 0
          ? `档案报告了 ${failureCount(data)} 条失败线索，但没有对应的节点详情。请结合事件与日志确认。`
          : '当前档案没有 status=failed 的节点记录。这不代表运行成功，也不能据此推断审批结果或运行中止的原因。'}>
        <ActionButton onClick={() => navigate('process', 'events')}>查看事件流</ActionButton>
        <ActionButton onClick={() => navigate('process', 'nodes')}>查看节点执行</ActionButton>
      </EmptyState>}
      {failures.map((failure, index) => (
        <article key={index} className="min-w-0 space-y-3 rounded-lg border border-border border-l-4 border-l-destructive bg-card p-4">
          <header className="flex min-w-0 flex-wrap items-center gap-2">
            <h3 className="min-w-0 text-sm font-semibold text-foreground [overflow-wrap:anywhere]">{failure.nodeTitle || failure.nodeId}</h3>
            <Badge className="bg-destructive/10 text-destructive">失败</Badge>
            <span className="text-xs text-muted-foreground">第 {text(failure.attempt)} 次执行</span>
            <span className="text-xs text-muted-foreground [overflow-wrap:anywhere]">{text(failure.executorType)}</span>
          </header>
          <p className="font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{failure.nodeId}</p>
          <p className="whitespace-pre-wrap rounded-lg bg-destructive/10 p-3 text-sm leading-6 text-destructive [overflow-wrap:anywhere]">{failure.error || '未记录错误信息'}</p>
          <div className="grid min-w-0 gap-3 xl:grid-cols-2">
            <Disclosure title="Input"><CodeBlock value={failure.execution?.input_json} label={`失败节点 ${failure.nodeId} Input`} /></Disclosure>
            <Disclosure title="Output"><CodeBlock value={failure.execution?.output_json} label={`失败节点 ${failure.nodeId} Output`} /></Disclosure>
          </div>
          <Disclosure title="Session 与执行记录">
            <p className="mb-3 text-xs text-muted-foreground [overflow-wrap:anywhere]">Embedded Session Key：{text(failure.embeddedSessionKey)}</p>
            <CodeBlock value={failure.execution} label={`失败节点 ${failure.nodeId} 执行记录`} empty="没有采集到对应执行次数的原始记录" />
          </Disclosure>
          <div className="flex flex-wrap gap-2"><ActionButton onClick={() => navigate('process', 'nodes')}>查看节点执行</ActionButton><ActionButton onClick={() => navigate('analysis')}>查看 AI 诊断</ActionButton></div>
        </article>
      ))}
      {data.failureSummary.rootCauseHints.length > 0 && <section className="rounded-lg border border-border p-4">
        <h3 className="text-sm font-semibold text-foreground">档案根因提示</h3>
        <p className="mt-1 text-xs text-muted-foreground">以下是档案提供的线索，不是已验证的因果结论。</p>
        <ul className="mt-3 list-inside list-disc space-y-2 text-sm text-muted-foreground [overflow-wrap:anywhere]">{data.failureSummary.rootCauseHints.map((hint, index) => <li key={index}>{hint}</li>)}</ul>
      </section>}
      {data.failureSummary.errorTimeline.length > 0 && <section className="rounded-lg border border-border p-4">
        <h3 className="text-sm font-semibold text-foreground">错误时间线</h3>
        <ol className="mt-3 space-y-3">{data.failureSummary.errorTimeline.map((event, index) => <li key={index} className="min-w-0 text-sm [overflow-wrap:anywhere]"><span className="text-xs text-muted-foreground">{formatTime(event.timestamp)}</span><p className="mt-1 font-medium text-foreground">{event.event}</p><p className="mt-1 text-muted-foreground">{event.detail}</p></li>)}</ol>
      </section>}
    </div>
  )
}

function severityClass(severity: RunArchiveDiagnosis['severity']): string {
  if (severity === 'critical') return 'bg-destructive/10 text-destructive'
  if (severity === 'high' || severity === 'medium') return 'bg-warning/10 text-warning-foreground'
  return 'bg-muted text-muted-foreground'
}

export function AnalysisSection({ data, navigate }: { data: RunArchiveData; navigate: Navigate }) {
  const analysis = data.analysis
  const emptyDescription = !analysis
    ? '档案没有提供 AI 分析数据。未提供诊断不等于已经检查且没有问题；此处不会发起新的诊断。'
    : analysis.status === 'failed'
      ? '本次分析失败，未返回诊断条目。可先核对已采集的节点、事件与日志。'
      : analysis.status === 'skipped'
        ? '本次分析已跳过，未返回诊断条目。跳过分析不代表运行正常，可继续查看执行证据。'
        : '分析已完成，但返回了 0 条诊断。这不代表运行没有问题，请结合执行记录核对。'
  return (
    <div className="min-w-0 space-y-4">
      {analysis && <div className="min-w-0 rounded-lg border border-border p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge className={analysis.status === 'failed' ? 'bg-destructive/10 text-destructive' : 'bg-muted text-muted-foreground'}>{analysisLabel(analysis)}</Badge>
          <Badge>{analysisSource(analysis.source)}</Badge>
          <span className="text-xs text-muted-foreground">诊断数：{analysis.diagnoses.length}</span>
        </div>
        {analysis.error && <p className="mt-3 rounded-lg bg-destructive/10 p-3 text-sm text-destructive [overflow-wrap:anywhere]">{analysis.error}</p>}
        {analysis.summary && <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-foreground [overflow-wrap:anywhere]">{analysis.summary}</p>}
        {analysis.diagnoses.length > 0 && analysis.status !== 'completed' && <p className="mt-3 text-xs leading-5 text-muted-foreground">本次分析未完成，但档案中的已有诊断仍展示如下；请结合其证据审阅。</p>}
      </div>}
      {!analysis?.diagnoses.length && <EmptyState title="暂无 AI 诊断条目" description={emptyDescription}>
        <ActionButton onClick={() => navigate('process', 'nodes')}>查看节点执行</ActionButton>
        <ActionButton onClick={() => navigate('process', 'events')}>查看事件流</ActionButton>
        {data.suggestedYaml && <ActionButton onClick={() => navigate('yaml')}>查看已有建议 YAML</ActionButton>}
      </EmptyState>}
      {analysis?.diagnoses.map((diagnosis, index) => (
        <article key={`${diagnosis.diagnosisId}-${index}`} className="min-w-0 space-y-4 rounded-lg border border-border border-l-4 border-l-primary/50 bg-card p-4">
          <header className="flex min-w-0 flex-wrap items-center gap-2">
            <h3 className="min-w-0 text-sm font-semibold text-foreground [overflow-wrap:anywhere]">诊断 #{index + 1}{diagnosis.nodeId ? ` · ${diagnosis.nodeId}` : ''}</h3>
            <Badge className={severityClass(diagnosis.severity)}>{diagnosis.severity}</Badge>
            <Badge>{diagnosis.failureMode}</Badge>
          </header>
          <section className="min-w-0"><h4 className="mb-2 text-xs font-semibold text-muted-foreground">根因分析 / Reasoning</h4><p className="whitespace-pre-wrap text-sm leading-6 text-foreground [overflow-wrap:anywhere]">{diagnosis.reasoning || '未提供根因分析'}</p></section>
          <section className="min-w-0"><h4 className="mb-2 text-xs font-semibold text-muted-foreground">证据事件</h4>
            {diagnosis.evidenceEventIds.length ? <ul className="list-inside list-disc space-y-1 font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{diagnosis.evidenceEventIds.map((eventId, i) => <li key={i}>{eventId}</li>)}</ul> : <p className="text-xs text-muted-foreground">未提供证据事件 ID</p>}
          </section>
          <section className="min-w-0"><h4 className="mb-2 text-xs font-semibold text-muted-foreground">修改建议 / SuggestedFixSpec</h4><p className="whitespace-pre-wrap text-sm leading-6 text-foreground [overflow-wrap:anywhere]">{diagnosis.suggestedFixSpec || '此诊断未提供修改建议；不代表无需修改。'}</p></section>
          {diagnosis.proposal && <Disclosure title={`建议修复 Patch（${diagnosis.proposal.operations.length} 项操作）`}><CodeBlock value={diagnosis.proposal} label={`诊断 ${index + 1} Patch`} /></Disclosure>}
          <div className="flex flex-wrap gap-2"><ActionButton onClick={() => navigate('process', 'events')}>查看事件流</ActionButton><ActionButton onClick={() => navigate('yaml')}>查看建议 YAML 与 Patch</ActionButton></div>
        </article>
      ))}
    </div>
  )
}
