import type { IssuePriority, IssueWindow } from '@avernet/clawevolve/server/services/evolution/issue-priority'
const percent = (value: number) => `${(value * 100).toFixed(1)}%`
function frequency(window: IssueWindow) {
  return window.occurrenceRate != null ? `发生率 ${percent(window.occurrenceRate)}`
    : window.sampleHitRate != null ? `已分析样本命中率 ${percent(window.sampleHitRate)}` : '频率待评估'
}
export default function IssuePrioritySummary({ priority }: { priority?: IssuePriority }) {
  if (!priority) return <span>影响、频率与覆盖率待评估</span>
  const label = priority.severity ? { critical: '严重', high: '高', medium: '中', low: '低' }[priority.severity] : '待评估'
  return <div className="space-y-1 text-xs leading-5 text-muted-foreground">
    <p><strong className="text-foreground">影响：{label}</strong> · {priority.severitySources.length ? `依据 ${priority.severitySources.length} 条真实诊断` : '缺少可靠严重性判断'} · 覆盖节点：{priority.nodes.join('、') || '未知'}</p>
    <p>近24小时：{priority.day.affected} 个问题运行 / {priority.day.related} 个相关运行 · 已分析 {priority.day.analyzed} · {frequency(priority.day)}</p>
    <p>近7天：{priority.week.affected} 个问题运行 / {priority.week.related} 个相关运行 · 已分析 {priority.week.analyzed} · {frequency(priority.week)}</p>
    <p>趋势：{priority.trend == null ? '暂无可靠对照' : { worsening: '恶化', improving: '改善', stable: '稳定' }[priority.trend]} · 最近发生：{priority.lastOccurredAtMs ? new Date(priority.lastOccurredAtMs).toLocaleString() : '运行时间未知'}</p>
    <details><summary className="cursor-pointer">排序与统计依据</summary><p>{priority.sortReason}</p><p>按真实运行去重；{priority.missingRunTimes} 个历史关联运行缺少时间，不纳入时间窗。相关运行以执行过相关节点为准；无节点信息时使用工作流运行。</p><p>严重性来源：{priority.severitySources.join('、') || '未知'}</p></details>
  </div>
}
