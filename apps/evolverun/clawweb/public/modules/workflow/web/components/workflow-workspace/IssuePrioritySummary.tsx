import type { IssuePriority, IssueWindow } from '@avernet/clawevolve/server/services/evolution/issue-priority'
const percent = (value: number) => `${(value * 100).toFixed(1)}%`
const severityTokens = { critical: 'bg-destructive text-primary-foreground', high: 'bg-warning-foreground text-primary-foreground',
  medium: 'bg-warning text-foreground', low: 'bg-primary text-primary-foreground', unknown: 'bg-muted text-foreground' }
function frequency(window: IssueWindow) {
  return window.occurrenceRate != null ? `全量发生率 ${percent(window.occurrenceRate)}`
    : window.sampleHitRate != null ? `可判定样本命中率 ${percent(window.sampleHitRate)}（不代表全量发生率）` : '频率待评估'
}
function windowSummary(label: string, value: IssueWindow) {
  return <div><p>{label}：记录此问题的运行 {value.affected} 次 / 执行相关节点的运行 {value.related} 次</p>
    <p>完成分析 {value.analyzed}/{value.related} · 可判定 {value.complete ?? '待统计'}/{value.related} · 证据未完整 {value.complete == null ? '待统计' : value.analyzed - value.complete} · 旧诊断证据 {value.legacyDiagnosed ?? '待统计'}</p>
    <p>可判定命中 {value.sampleHits ?? '待统计'}/{value.complete ?? '待统计'} · {frequency(value)}</p></div>
}
export default function IssuePrioritySummary({ priority }: { priority?: IssuePriority }) {
  if (!priority) return <span>影响、频率与覆盖率待评估</span>
  const label = priority.severity ? { critical: '严重', high: '高', medium: '中', low: '低' }[priority.severity] : '待评估'
  return <div className="space-y-2 text-xs leading-5 text-muted-foreground">
    <p><strong aria-label={`影响等级：${label}`} className={`inline-flex rounded-md px-2 py-1 ${severityTokens[priority.severity ?? 'unknown']}`}>影响：{label}</strong> · {priority.severitySources.length ? `依据 ${priority.severitySources.length} 条真实诊断` : '缺少可靠严重性判断'} · 相关节点：{priority.nodes.join('、') || '未知，按工作流运行统计'}</p>
    {windowSummary('近24小时', priority.day)}{windowSummary('近7天', priority.week)}
    <p>趋势：{priority.trend == null ? '暂无可靠对照' : { worsening: '恶化', improving: '改善', stable: '稳定' }[priority.trend]} · 最近发生：{priority.lastOccurredAtMs ? new Date(priority.lastOccurredAtMs).toLocaleString() : '运行时间未知'}</p>
    <details><summary className="cursor-pointer">统计口径与排序依据</summary><p>{priority.sortReason}</p>
      <p>按运行 ID 去重，以真实运行时间划分滚动24小时、7天和前7天；同一运行多次分析不重复计数。{priority.missingRunTimes} 个历史关联运行缺少时间，不纳入窗口。</p>
      <p>分母为执行过相关节点的运行，并包含已有问题证据的运行；节点未知时才使用工作流运行。完成分析仅计算覆盖本问题节点的有效结果；最新结果存在未知项时仍算完成，但不算可判定。旧诊断单独列出，不冒充完整分析。</p>
      <p>只有所有相关运行均可判定时才展示全量发生率；其他情况只展示可判定样本命中率，缺分析不算健康。严重性来源：{priority.severitySources.join('、') || '未知'}</p>
    </details>
  </div>
}
