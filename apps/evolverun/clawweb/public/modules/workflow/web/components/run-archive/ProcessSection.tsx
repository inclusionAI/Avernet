import type { RunArchiveData } from '@avernet/clawweb-shared/web/types'
import { CodeBlock, Definition, Disclosure, ViewButton } from './ArchivePrimitives'
import { formatDuration, formatTime, recordedCount, text, type ProcessView } from './archive-helpers'
import { EventTimeline, RunLogsView } from './EventAndLogViews'
import NodeExecutionsView from './NodeExecutionsView'

export default function ProcessSection({ data, view, onViewChange }: {
  data: RunArchiveData
  view: ProcessView
  onViewChange: (view: ProcessView) => void
}) {
  const views: Array<{ id: ProcessView; label: string; count?: number }> = [
    { id: 'info', label: '流程信息' },
    { id: 'nodes', label: '节点执行', count: data.nodeExecutions.length },
    { id: 'events', label: '事件流', count: data.flowEvents.length },
    { id: 'logs', label: '运行日志', count: data.runLogs.length },
  ]
  return (
    <div className="min-w-0 space-y-4">
      <nav aria-label="运行过程视图" className="flex min-w-0 flex-wrap gap-2">
        {views.map(item => <ViewButton key={item.id} active={view === item.id} label={item.label} onClick={() => onViewChange(item.id)}>{item.label}{item.count !== undefined && <span className="text-xs tabular-nums">{item.count}</span>}</ViewButton>)}
      </nav>
      <section className="min-w-0" aria-label={views.find(item => item.id === view)?.label}>
        {view === 'info' && <ProcessInfo data={data} />}
        {view === 'nodes' && <NodeExecutionsView data={data} />}
        {view === 'events' && <EventTimeline data={data} />}
        {view === 'logs' && <RunLogsView data={data} />}
      </section>
    </div>
  )
}

function ProcessInfo({ data }: { data: RunArchiveData }) {
  const flow = data.flowRun
  return (
    <div className="min-w-0 space-y-4">
      {!flow && <p className="rounded-lg bg-muted p-3 text-sm text-muted-foreground">档案未提供流程运行记录。其他已采集的节点、事件和日志仍可查看。</p>}
      <dl className="grid min-w-0 grid-cols-2 gap-x-5 gap-y-4 rounded-lg border border-border p-4 lg:grid-cols-3">
        <Definition label="工作流 ID" mono>{text(flow?.workflow_id)}</Definition>
        <Definition label="Flow ID" mono>{text(flow?.flow_id, data.archive.flowId)}</Definition>
        <Definition label="原始运行状态">{text(flow?.status)}</Definition>
        <Definition label="开始时间">{formatTime(flow?.started_at)}</Definition>
        <Definition label="结束时间">{formatTime(flow?.completed_at)}</Definition>
        <Definition label="耗时">{formatDuration(flow?.total_duration_ms ?? flow?.duration_ms)}</Definition>
        <Definition label="流程节点总数">{recordedCount(flow?.node_count)}</Definition>
        <Definition label="已采集执行记录">{data.nodeExecutions.length} 条</Definition>
        <Definition label="档案报告失败节点">{recordedCount(data.failureSummary.failedNodeCount)}</Definition>
      </dl>
      <p className="text-xs leading-5 text-muted-foreground">流程节点总数来自 flowRun.node_count，不使用执行条目数替代；重复执行会产生多条记录，缺失记录也不等于跳过。</p>
      <Disclosure title="流程运行原始记录" open><CodeBlock value={flow} label="流程运行原始记录 JSON" /></Disclosure>
      <div className="flex min-w-0 flex-wrap gap-2 text-xs text-muted-foreground">
        <span>节点步骤追踪：{data.nodeStepTraces.length} 条</span><span>执行步骤日志：{data.executionStepLogs.length} 条</span><span>Trace：{data.langfuseTraces.length} 条</span><span>Observation：{data.langfuseObservations.length} 条</span>
      </div>
      {data.nodeStepTraces.length > 0 && <Disclosure title="节点步骤追踪"><CodeBlock value={data.nodeStepTraces} label="节点步骤追踪 JSON" /></Disclosure>}
      {data.executionStepLogs.length > 0 && <Disclosure title="执行步骤日志"><CodeBlock value={data.executionStepLogs} label="执行步骤日志 JSON" /></Disclosure>}
      {data.langfuseTraces.length > 0 && <Disclosure title="Trace 数据"><CodeBlock value={data.langfuseTraces} label="Trace JSON" /></Disclosure>}
      {data.langfuseObservations.length > 0 && <Disclosure title="Observation 数据"><CodeBlock value={data.langfuseObservations} label="Observation JSON" /></Disclosure>}
    </div>
  )
}
