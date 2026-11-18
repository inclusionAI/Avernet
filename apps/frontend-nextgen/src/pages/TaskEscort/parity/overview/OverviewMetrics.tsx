import { overviewDuration, overviewStatistics } from '@/domain/taskEscortParity/overview';
import type { OverviewHealth, OverviewReadState, OverviewRuns } from '@/domain/taskEscortParity/overviewTypes';
import { Button, ReadFeedback } from './OverviewShared';

export function OverviewMetrics({
  metrics,
  health,
  days,
}: {
  metrics: OverviewReadState<OverviewRuns>;
  health: OverviewReadState<OverviewHealth>;
  days: string;
}) {
  const stats = overviewStatistics(metrics.data?.statusCounts ?? null),
    h = health.data;
  const label = days === 'today' ? '今天' : days === 'yesterday' ? '昨天' : `近 ${days} 天`;
  return (
    <>
      <section aria-label="工作流关键指标" className="tcov-kpis tcov-card">
        {[
          {
            label: '健康度',
            value: h?.overallScore ?? '—',
            detail:
              h?.overallScore === null || h?.overallScore === undefined
                ? '等待健康数据'
                : h.overallScore >= 80
                ? '运行稳定'
                : h.overallScore >= 60
                ? '需要关注'
                : '建议优先处理',
            danger: h?.overallScore !== null && h?.overallScore !== undefined && h.overallScore < 60,
          },
          {
            label: '运行成功率',
            value:
              stats?.successRate === null || stats?.successRate === undefined
                ? '—'
                : `${Math.round(stats.successRate)}%`,
            detail: `${label} · 成功 / 终态`,
          },
          {
            label: '异常结束',
            value: stats?.abnormal ?? '—',
            detail: '失败、终止或取消',
            danger: Boolean(stats?.abnormal),
          },
          { label: '节点耗时 P95', value: overviewDuration(h?.p95DurationMs), detail: '最慢节点 P95 口径' },
          { label: '总运行实例', value: stats?.total ?? '—', detail: `${label} · 全部状态` },
        ].map((metric) => (
          <div key={metric.label} className="px-5 py-4">
            <p className="text-xs text-muted-foreground">{metric.label}</p>
            <p
              className={`mt-1 text-2xl font-semibold tabular-nums tracking-tight ${
                metric.danger ? 'text-destructive' : 'text-foreground'
              }`}
            >
              {metric.value}
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground">{metric.detail}</p>
          </div>
        ))}
      </section>
      <section aria-label="当前运行状态" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ['运行中', stats?.running],
          ['等待中', stats?.waiting],
          ['阻塞', stats?.blocked],
          ['排队中', stats?.queued],
        ].map(([label, value], index) => (
          <div
            key={String(label)}
            className={`rounded-lg px-3 py-2 text-xs font-medium ${
              index === 0
                ? 'bg-primary/10 text-primary'
                : index < 3
                ? 'bg-warning/10 text-warning'
                : 'bg-muted text-muted-foreground'
            }`}
          >
            {label} {value ?? '—'}
          </div>
        ))}
      </section>
      {(metrics.loading || metrics.error) && <ReadFeedback state={metrics} label="运行指标" />}
      {metrics.data && metrics.data.statusCounts === null && (
        <p className="text-xs text-muted-foreground">接口未提供全量状态统计；不以分页运行样本推算指标。</p>
      )}
      {health.error && <ReadFeedback state={health} label="健康指标" />}
      {(days === 'today' || days === 'yesterday') && (
        <p className="text-[11px] text-muted-foreground">
          运行计数按所选自然日；健康指标为接口最近 1 天口径，不冒充自然日健康数据。
        </p>
      )}
    </>
  );
}
export function OverviewRisk({
  state,
  onNode,
}: {
  state: OverviewReadState<OverviewHealth>;
  onNode: (id: string) => void;
}) {
  const health = state.data;
  return (
    <section className="tcov-card p-4" aria-label="运行风险摘要">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">运行风险</h3>
          <p className="mt-1 text-[11px] text-muted-foreground">优先关注影响成功率与耗时的节点</p>
        </div>
        <span className="rounded-full bg-muted px-2 py-1 text-[10px] text-muted-foreground">
          {health?.overallScore === null || health?.overallScore === undefined ? '待计算' : `${health.overallScore} 分`}
        </span>
      </div>
      <div className="mt-4 space-y-2">
        {[
          { label: '耗时瓶颈', node: health?.bottleneckNode },
          { label: '脆弱节点', node: health?.fragileNode },
        ].map(({ label, node }) => (
          <Button
            key={label}
            variant="ghost"
            disabled={!node}
            onClick={() => node && onNode(node)}
            className="h-auto w-full justify-between gap-3 bg-muted/50 px-3 py-2"
          >
            <span className="text-xs text-muted-foreground">{label}</span>
            <span className="break-all font-mono text-xs">{node || '—'}</span>
          </Button>
        ))}
      </div>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">
        {state.loading
          ? '风险数据加载中…'
          : state.error
          ? '风险数据读取失败，未生成推测建议'
          : health?.recommendation || '接口未提供风险建议'}
      </p>
    </section>
  );
}
