import type { RunArchiveData } from '@avernet/clawweb-shared/web/types'
import { ActionButton, Badge, Definition, StatusBadge } from './ArchivePrimitives'
import {
  analysisLabel, failureCount, failureEntries, formatDuration, formatTime,
  nodeStatus, recordedCount, STATUS_ORDER, statusClass, statusLabel, text,
  type Navigate,
} from './archive-helpers'

export default function OverviewSection({ data, runStatus, navigate }: {
  data: RunArchiveData
  runStatus?: string | null
  navigate: Navigate
}) {
  const flow = data.flowRun
  const failures = failureEntries(data)
  const failedCount = failureCount(data)
  const nodeIds = new Set(data.nodeExecutions.map(node => text(node.node_id, '')).filter(Boolean))
  const counts = STATUS_ORDER.map(status => ({
    status, count: data.nodeExecutions.filter(node => nodeStatus(node.status) === status).length,
  })).filter(item => item.count > 0)
  const collected = [
    { label: '已采集节点', value: nodeIds.size, detail: `${data.nodeExecutions.length} 条执行记录`, action: () => navigate('process', 'nodes') },
    { label: '已采集事件', value: data.flowEvents.length, detail: '查看事件流', action: () => navigate('process', 'events') },
    { label: '已采集日志', value: data.runLogs.length, detail: '查看运行日志', action: () => navigate('process', 'logs') },
    { label: '诊断条目', value: data.analysis?.diagnoses.length ?? 0, detail: analysisLabel(data.analysis), action: () => navigate('analysis') },
  ]

  return (
    <div className="min-w-0 space-y-5">
      <div className="grid min-w-0 gap-4 rounded-lg border border-border bg-muted/30 p-4 sm:grid-cols-2">
        <div className="min-w-0">
          <p className="mb-2 text-xs text-muted-foreground">运行状态</p>
          <StatusBadge status={runStatus?.trim() || flow?.status} />
          <p className="mt-2 text-xs leading-5 text-muted-foreground">来自运行记录，独立于下方档案采集和分析状态。</p>
        </div>
        <div className="min-w-0">
          <p className="mb-2 text-xs text-muted-foreground">档案聚合状态</p>
          <Badge className={data.archive.status === 'partial' ? 'bg-warning/10 text-warning-foreground' : 'bg-primary/10 text-primary'}>
            {data.archive.status === 'partial' ? '部分采集 · partial' : '采集完成 · completed'}
          </Badge>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">采集完成不代表运行成功，也不表示所有数据源都有记录。</p>
        </div>
      </div>

      <dl className="grid min-w-0 grid-cols-2 gap-3 xl:grid-cols-4">
        {collected.map(item => (
          <div key={item.label} className="min-w-0 rounded-lg border border-border bg-card p-3">
            <dt className="text-xs text-muted-foreground">{item.label}</dt>
            <dd className="my-2 text-2xl font-semibold tabular-nums text-foreground">{item.value}</dd>
            <button type="button" onClick={item.action} className="max-w-full rounded text-left text-xs leading-5 text-primary [overflow-wrap:anywhere] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
              {item.detail} <span aria-hidden="true">→</span>
            </button>
          </div>
        ))}
      </dl>

      <dl className="grid min-w-0 grid-cols-2 gap-x-5 gap-y-4 rounded-lg border border-border p-4 lg:grid-cols-3">
        <Definition label="流程节点总数">{recordedCount(flow?.node_count)}</Definition>
        <Definition label="已采集执行记录">{data.nodeExecutions.length} 条（包含不同执行次数）</Definition>
        <Definition label="运行耗时">{formatDuration(flow?.total_duration_ms ?? flow?.duration_ms)}</Definition>
        <Definition label="开始时间">{formatTime(flow?.started_at)}</Definition>
        <Definition label="结束时间">{formatTime(flow?.completed_at)}</Definition>
        <Definition label="建议 YAML">{data.suggestedYaml?.yamlContent?.trim() ? '已提供 · 未应用' : '未提供'}</Definition>
      </dl>

      <section className="rounded-lg border border-border p-4" aria-label="已采集执行状态分布">
        <h3 className="text-sm font-semibold text-foreground">执行记录状态</h3>
        <div className="mt-3 flex flex-wrap gap-2">
          {counts.length ? counts.map(item => <Badge key={item.status} className={statusClass(item.status)}>{statusLabel(item.status)} {item.count}</Badge>)
            : <p className="text-sm text-muted-foreground">未采集到节点执行记录。</p>}
        </div>
        <p className="mt-3 text-xs leading-5 text-muted-foreground">统计仅基于已采集执行条目；流程节点总数与执行条目数口径不同。没有执行记录的节点不推断为跳过。</p>
      </section>

      <div className="grid min-w-0 gap-4 xl:grid-cols-2">
        <section className="min-w-0 rounded-lg border border-border p-4">
          <h3 className="text-sm font-semibold text-foreground">错误线索</h3>
          {failedCount > 0 ? (
            <>
              <p className="mt-2 text-sm text-destructive">档案记录了 {failedCount} 条失败节点线索。</p>
              <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
                {failures.slice(0, 3).map((failure, i) => <li key={i} className="[overflow-wrap:anywhere]"><strong className="font-medium text-foreground">{failure.nodeTitle || failure.nodeId}</strong>：{failure.error || '未记录错误信息'}</li>)}
              </ul>
              <div className="mt-4"><ActionButton onClick={() => navigate('errors')}>查看错误详情</ActionButton></div>
            </>
          ) : (
            <>
              <p className="mt-2 text-sm text-foreground">未采集到失败节点</p>
              <p className="mt-2 text-xs leading-6 text-muted-foreground">不等于运行成功或全链路无异常。运行中止、审批结果与执行报错是不同事实，不能仅凭记录先后推断因果。</p>
              <div className="mt-4"><ActionButton onClick={() => navigate('process', 'events')}>查看事件流</ActionButton></div>
            </>
          )}
        </section>
        <section className="min-w-0 rounded-lg border border-border p-4">
          <h3 className="text-sm font-semibold text-foreground">下一步</h3>
          <p className="mt-2 text-sm text-foreground">{analysisLabel(data.analysis)}</p>
          <p className="mt-2 text-xs leading-6 text-muted-foreground">
            {data.analysis?.diagnoses.length ? '结合节点输入、输出和事件证据核对诊断，再人工审阅建议。' : '先核对执行记录与事件流；未提供诊断不表示 AI 已检查且没有问题。'}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <ActionButton onClick={() => navigate('process', 'nodes')}>查看节点执行</ActionButton>
            <ActionButton onClick={() => navigate('analysis')}>查看 AI 诊断</ActionButton>
            {data.suggestedYaml && <ActionButton onClick={() => navigate('yaml')}>查看建议 YAML</ActionButton>}
          </div>
        </section>
      </div>
    </div>
  )
}
