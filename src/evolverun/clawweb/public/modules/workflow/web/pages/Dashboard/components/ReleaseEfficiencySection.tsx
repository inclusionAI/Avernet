import type { IDashboardReleaseEfficiency, MetricKey } from '../../../types/dashboard'
import { formatDuration } from '../utils'

interface ReleaseEfficiencySectionProps {
  data: IDashboardReleaseEfficiency | undefined
  onTrendClick?: (metric: MetricKey, label: string) => void
}

/**
 * 发布效能:发布次数 / 发布成功率 / 回滚率 / 平均交付拖期(四小卡,可点看趋势)。
 * 对应"开发→上线阶段"的效能指标。明细不再单列——下方"工作流列表"全景表已含
 * 部署次数/研发周期/最近部署/首批成功率/回滚次数,点行进 L3 看每次发布事件。
 * 注:"交付拖期"= deployed_at − 最近一次 save,口径是"人/团队从改完到点上线"的拖期,
 * 不是机制冻结耗时(那是秒级、无业务故事)。研发效能看交付节奏,不看机制效率。
 */
export function ReleaseEfficiencySection({ data, onTrendClick }: ReleaseEfficiencySectionProps) {
  // 当前库不可算(SQLite 无 workflow_deploy_history)→ 占位,不渲染空表
  if (data?.available === false) {
    return (
      <div className="rounded-xl bg-white p-5 shadow-sm">
        <div className="mb-1 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900">发布效能</h3>
          <span className="text-xs text-gray-400">暂不可算</span>
        </div>
        <div className="py-6 text-center text-sm text-gray-400">
          当前库无 <code className="rounded bg-gray-100 px-1 text-gray-600">workflow_deploy_history</code> 表(MySQL only)。<br />
          切到 MySQL 库或等发布机制落地后出真数。
        </div>
      </div>
    )
  }

  const releaseCount = data?.releaseCount ?? 0
  const rollbackCount = data?.rollbackCount ?? 0
  const rollbackRate = data?.rollbackRate ?? null
  const successRate = data?.successRate ?? null
  const avgDur = data?.avgReleaseDurationMs ?? null

  const metrics: Array<{ label: string; value: string; sub?: string; tone: 'ok' | 'warn' | 'neutral'; metric: MetricKey }> = [
    { label: '发布次数', value: String(releaseCount), sub: 'deploy 事件数 · 区别于"上线工作流数"', tone: 'neutral', metric: 'deploys' },
    {
      label: '发布成功率',
      value: successRate !== null ? `${(successRate * 100).toFixed(0)}%` : '—',
      sub: '前 10 条终态/生产触发',
      tone: 'ok',
      metric: 'releaseSuccessRate',
    },
    {
      label: '回滚率',
      value: rollbackRate !== null ? `${(rollbackRate * 100).toFixed(0)}%` : '—',
      sub: `${rollbackCount} 次回滚`,
      tone: rollbackCount > 0 ? 'warn' : 'ok',
      metric: 'rollbackRate',
    },
    {
      label: '平均交付拖期',
      value: avgDur !== null ? formatDuration(avgDur) : '—',
      sub: 'save → deploy · 人/团队拖期',
      tone: 'neutral',
      metric: 'deliveryLagHours',
    },
  ]

  const toneClass = {
    ok: 'text-emerald-600',
    warn: 'text-amber-600',
    neutral: 'text-gray-900',
  }

  return (
    <div className="rounded-xl bg-white p-5 shadow-sm">
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">发布效能</h3>
        <span className="text-xs text-gray-400">口径跟右上角周期 · 点小卡看趋势 · 交付拖期=人/团队上线拖期</span>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {metrics.map((m) => (
          <div
            key={m.label}
            role="button"
            tabIndex={0}
            onClick={() => onTrendClick?.(m.metric, m.label)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onTrendClick?.(m.metric, m.label) }}
            className="cursor-pointer rounded-lg border border-gray-100 bg-gray-50/60 p-3 transition hover:border-blue-200 hover:bg-blue-50/40"
          >
            <div className="text-xs text-gray-500">{m.label}</div>
            <div className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass[m.tone]}`}>{m.value}</div>
            {m.sub && <div className="mt-0.5 text-[11px] text-gray-400">{m.sub}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}