import { Empty } from '@/components/ui/Empty';
import { formatDashboardCount } from '@/domain/taskEscortWorkspace/dashboard';
import type {
  DashboardDailyTrend,
  DashboardFailureHotspots,
  DashboardResource,
  DashboardWidgetRange,
} from '@/domain/taskEscortWorkspace/dashboardTypes';
import { DashboardReadState, DashboardTooltip, DashboardWidgetSelector } from './DashboardControls';
import { DashboardQualitySvg } from './DashboardQualitySvg';
import { DashboardTrendDetails } from './DashboardTrendDetails';

interface RangeProps {
  range: DashboardWidgetRange;
  globalLabel: string;
  onRangeChange: (value: DashboardWidgetRange) => void;
}
const panel = 'min-w-0 rounded-xl border border-border bg-card p-5 text-card-foreground';

export function DashboardTrend({
  resource,
  ...range
}: RangeProps & { resource: DashboardResource<DashboardDailyTrend> }) {
  return (
    <section className={panel} aria-label="运行质量趋势">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">运行质量趋势</h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">成功率上升且异常结束数下降，才代表运行质量改善</p>
        </div>
        <DashboardWidgetSelector
          title="运行质量趋势"
          value={range.range}
          globalLabel={range.globalLabel}
          onChange={range.onRangeChange}
        />
      </div>
      <div className="my-4 flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <i className="size-2 rounded-full bg-success" />
          运行成功率
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="size-2 rounded-sm bg-destructive/40" />
          异常结束数
        </span>
        <span>同一终态样本口径</span>
      </div>
      <DashboardReadState resource={resource} title="运行质量趋势">
        {(data) =>
          data.dates.length ? (
            <>
              <DashboardQualitySvg points={data.dates} />
              <DashboardTrendDetails points={data.dates} />
            </>
          ) : (
            <Empty compact title="所选周期暂无运行趋势" description="仅展示接口返回的真实趋势，不补造曲线" />
          )
        }
      </DashboardReadState>
    </section>
  );
}
export function DashboardHotspots({
  resource,
  ...range
}: RangeProps & { resource: DashboardResource<DashboardFailureHotspots> }) {
  return (
    <section className={panel} aria-label="异常热点">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">异常热点</h2>
          <p className="mt-1 text-xs text-muted-foreground">按失败归因聚合 · Top 10</p>
        </div>
        <DashboardWidgetSelector
          title="异常热点"
          value={range.range}
          globalLabel={range.globalLabel}
          onChange={range.onRangeChange}
        />
      </div>
      <DashboardReadState resource={resource} title="异常热点">
        {(data) => {
          if (!data.hotspots.length) return <Empty compact title="所选周期暂无异常热点" />;
          const max = Math.max(1, ...data.hotspots.flatMap((row) => (row.count === null ? [] : [row.count])));
          return (
            <ul className="mt-5 space-y-4" aria-label="异常热点分布">
              {data.hotspots.map((row, index) => (
                <li key={`${row.nodeLabel}-${index}`} className="min-w-0">
                  <div className="flex items-start justify-between gap-3 text-xs">
                    <span className="min-w-0 break-words font-medium">{row.nodeLabel || '未命名节点'}</span>
                    <span className="shrink-0 tabular-nums text-muted-foreground">
                      {formatDashboardCount(row.count)} 次 ·{' '}
                      {row.sharePct === null || row.sharePct === undefined ? '—' : `${row.sharePct.toFixed(1)}%`}
                    </span>
                  </div>
                  <div
                    className="mt-2 h-2 overflow-hidden rounded-full bg-muted"
                    role="img"
                    aria-label={`${row.nodeLabel}，${formatDashboardCount(row.count)} 次异常`}
                  >
                    {row.count !== null && (
                      <div
                        className="h-full rounded-full bg-destructive/40"
                        style={{ width: `${Math.min(100, (row.count / max) * 100)}%` }}
                      />
                    )}
                  </div>
                  <p className="mt-1 break-words text-xs text-muted-foreground">
                    {row.workflowTitle}
                    {row.sceneName ? ` · ${row.sceneName}` : ''}
                  </p>
                  {row.exampleError && (
                    <DashboardTooltip label={row.exampleError}>
                      <p className="mt-1 line-clamp-2 break-all text-xs text-muted-foreground" tabIndex={0}>
                        {row.exampleError}
                      </p>
                    </DashboardTooltip>
                  )}
                </li>
              ))}
            </ul>
          );
        }}
      </DashboardReadState>
    </section>
  );
}
