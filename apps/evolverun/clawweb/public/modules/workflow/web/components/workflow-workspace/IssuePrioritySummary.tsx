import type { IssuePriority } from '@avernet/clawevolve/server/services/evolution/issue-priority'
const severityTokens = { critical: 'bg-destructive text-primary-foreground', high: 'bg-warning-foreground text-primary-foreground',
  medium: 'bg-warning text-foreground', low: 'bg-primary text-primary-foreground', unknown: 'bg-muted text-foreground' }
export default function IssuePrioritySummary({ priority }: { priority?: IssuePriority }) {
  if (!priority) return null
  const label = priority.severity ? { critical: '严重', high: '高', medium: '中', low: '低' }[priority.severity] : '待评估'
  return <div className="space-y-1 text-xs leading-5 text-muted-foreground">
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1"><strong aria-label={`影响等级：${label}`} className={`inline-flex rounded-md px-2 py-1 ${severityTokens[priority.severity ?? 'unknown']}`}>影响：{label}</strong>
      <span>近24小时发现 {priority.day.affected} 次</span><span>· 近7天发现 {priority.week.affected} 次</span>
    </p>
    <p>最近发生：{priority.lastOccurredAtMs ? new Date(priority.lastOccurredAtMs).toLocaleString() : '运行时间未知'}</p>
  </div>
}
