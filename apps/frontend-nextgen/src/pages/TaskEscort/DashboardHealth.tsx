import { Activity, ArrowDownRight, ArrowUpRight, CircleAlert, Clock3, ShieldCheck } from 'lucide-react';
import type { ViewProps } from './types';
import { Badge, Button, Panel } from './ui';

interface DashboardHealthProps extends ViewProps {
  toRuns: (filter?: string) => void;
}

export function DashboardHealth({ demo, toRuns }: DashboardHealthProps) {
  const m = demo.metrics;
  const metrics = [
    {
      label: '总运行数',
      value: m.runs.toLocaleString(),
      note: '已接入工作流的全部运行',
      color: 'text-primary',
      trend: '+12.8%',
      icon: Activity,
      click: () => toRuns(),
    },
    {
      label: '运行成功率',
      value: `${m.successRate}%`,
      note: '成功 / 全部已结束运行',
      color: 'text-success',
      trend: '−2.4pp',
      icon: ShieldCheck,
      click: () => toRuns('success'),
    },
    {
      label: '异常结束',
      value: m.anomalies.toLocaleString(),
      note: '失败、终止或取消',
      color: 'text-destructive',
      trend: '+18',
      icon: CircleAlert,
      click: () => toRuns('failed'),
    },
    {
      label: '成功运行耗时 P50',
      value: `${m.p50}s`,
      note: '端到端耗时中位数',
      color: 'text-foreground',
      trend: '+115.4s',
      icon: Clock3,
      click: () => toRuns(),
    },
  ];
  return (
    <Panel
      title="全局运行健康"
      description="管理全局运行，及时发现异常与性能波动"
      action={
        <Badge tone="neutral">近{demo.period === '30d' ? ' 30 天' : demo.period === '7d' ? ' 7 天' : ' 1 天'}</Badge>
      }
    >
      <div className="grid grid-cols-2 gap-3 2xl:grid-cols-4">
        {metrics.map((item) => {
          const Icon = item.icon;
          return (
            <Button
              key={item.label}
              variant="ghost"
              className="metric-card h-auto min-w-0 flex-col items-start gap-0 whitespace-normal p-4 text-left"
              onClick={item.click}
            >
              <span className="mb-4 flex w-full items-center justify-between text-muted-foreground">
                <Icon size={17} className={item.color} />
                <span
                  className={`text-[10px] font-medium ${
                    item.trend.startsWith('+12') ? 'text-success' : 'text-destructive'
                  }`}
                >
                  {item.trend.startsWith('+12') ? (
                    <ArrowUpRight size={11} className="inline" />
                  ) : (
                    <ArrowDownRight size={11} className="inline" />
                  )}
                  {item.trend}
                </span>
              </span>
              <strong className={`stat-value ${item.color}`}>{item.value}</strong>
              <span className="mt-2 text-xs font-medium">{item.label}</span>
              <span className="mt-1 text-[10px] font-normal leading-4 text-muted-foreground">{item.note}</span>
            </Button>
          );
        })}
      </div>
      <div className="mt-5 grid grid-cols-4 gap-2 border-t border-border pt-4">
        {[
          ['运行中', m.running, 'primary'],
          ['等待中', m.waiting, 'warning'],
          ['阻塞', m.blocked, 'destructive'],
          ['排队中', m.queued, 'muted-foreground'],
        ].map(([label, value, color]) => (
          <Button
            key={label}
            variant="ghost"
            className="h-8 justify-between rounded-md bg-muted/40 px-2.5 text-[11px]"
            onClick={() => toRuns(label === '运行中' ? 'running' : label === '等待中' ? 'waiting' : 'all')}
          >
            <span className="text-muted-foreground">{label}</span>
            <span className={`tabular-nums text-${color}`}>{value}</span>
          </Button>
        ))}
      </div>
    </Panel>
  );
}
