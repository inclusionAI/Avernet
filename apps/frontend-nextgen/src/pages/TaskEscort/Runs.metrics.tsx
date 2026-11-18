import { Activity, CheckCheck, Clock3, GitBranch } from 'lucide-react';
import { formatDuration } from './formatting';
import type { ViewProps } from './types';
import { MiniLine } from './ui';
export function RunMetrics({ demo }: ViewProps) {
  const metrics = [
    {
      label: '运行总数',
      value: demo.metrics.runs.toLocaleString(),
      unit: '次',
      hint: '当前统计周期',
      icon: Activity,
      line: demo.metrics.series,
    },
    {
      label: '运行成功率',
      value: String(demo.metrics.successRate),
      unit: '%',
      hint: '已完成任务的成功占比',
      icon: CheckCheck,
      line: demo.metrics.successSeries,
    },
    {
      label: 'P50 执行耗时',
      value: formatDuration(demo.metrics.p50),
      unit: '',
      hint: '当前周期中位执行耗时',
      icon: Clock3,
    },
    {
      label: '进行中与等待',
      value: String(demo.metrics.running + demo.metrics.waiting),
      unit: '次',
      hint: `${demo.metrics.running} 运行中 · ${demo.metrics.waiting} 等待中`,
      icon: GitBranch,
    },
  ];
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map(({ label, value, unit, hint, icon: Icon, line }) => (
        <section key={label} className="panel relative min-w-0 overflow-hidden">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-xs font-medium text-muted-foreground">{label}</h3>
            <Icon className="size-4 text-muted-foreground/70" aria-hidden="true" />
          </div>
          <div className="mt-4 flex items-baseline gap-1.5">
            <span className="text-[28px] font-semibold leading-9 tracking-tight tabular-nums">{value}</span>
            <span className="text-xs text-muted-foreground">{unit}</span>
          </div>
          <div className="mt-2 flex items-end justify-between gap-3">
            <p className="text-[11px] text-muted-foreground">{hint}</p>
            {line && (
              <div className="h-6 w-20 shrink-0 opacity-70" aria-hidden="true">
                <MiniLine values={line} height={24} />
              </div>
            )}
          </div>
        </section>
      ))}
    </div>
  );
}
