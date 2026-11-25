import { ArrowRight } from 'lucide-react';
import type { ViewProps } from './types';
import { Button, Panel } from './ui';

interface TrendProps {
  values: number[];
  success: number[];
  labels: string[];
}

function Trend({ values, success, labels }: TrendProps) {
  const max = Math.max(100, Math.ceil(Math.max(0, ...values) / 100) * 100);
  const line = success
    .map((v, i) => `${48 + (i * 620) / Math.max(1, success.length - 1)},${140 - (v - 70) * 3.7}`)
    .join(' ');
  return (
    <div className="chart-wrap">
      <svg viewBox="0 0 720 210" role="img" aria-label="运行量与成功率趋势" className="w-full">
        {[0, 1, 2, 3].map((i) => (
          <g key={i}>
            <line
              x1="42"
              x2="684"
              y1={20 + i * 46}
              y2={20 + i * 46}
              stroke="hsl(var(--border))"
              strokeDasharray="3 4"
            />
            <text x="30" y={24 + i * 46} textAnchor="end" className="chart-label">
              {Math.round(max * (1 - i / 3))}
            </text>
          </g>
        ))}
        {values.map((v, i) => (
          <g key={i}>
            <rect
              x={48 + (i * 620) / Math.max(1, values.length - 1) - 12}
              y={158 - (v / max) * 132}
              width="24"
              height={(v / max) * 132}
              rx="3"
              fill="hsl(var(--primary) / .13)"
            />
            <text
              x={48 + (i * 620) / Math.max(1, values.length - 1)}
              y="186"
              textAnchor="middle"
              className="chart-label"
            >
              {labels[i]}
            </text>
          </g>
        ))}
        <polyline
          points={line}
          stroke="hsl(var(--primary))"
          strokeWidth="2.5"
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {success.map((v, i) => (
          <circle
            key={i}
            cx={48 + (i * 620) / Math.max(1, values.length - 1)}
            cy={140 - (v - 70) * 3.7}
            r="3.5"
            fill="hsl(var(--background))"
            stroke="hsl(var(--primary))"
            strokeWidth="2"
          />
        ))}
      </svg>
    </div>
  );
}

interface DashboardQualityProps extends ViewProps {
  toIssues: (filter: string) => void;
}

export function DashboardQuality({ demo, toIssues }: DashboardQualityProps) {
  const m = demo.metrics;
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
      <Panel
        title="运行质量趋势"
        description="运行量与成功率的变化"
        action={
          <div className="flex gap-3 text-[10px] text-muted-foreground">
            <span>
              <i className="mr-1 inline-block size-2 rounded-sm bg-primary/20" />
              运行量
            </span>
            <span>
              <i className="mr-1 inline-block h-0.5 w-3 bg-primary" />
              成功率
            </span>
          </div>
        }
      >
        <Trend values={m.series} success={m.successSeries} labels={m.labels} />
      </Panel>
      <Panel
        title="高频异常分布"
        description="按受影响运行聚合"
        action={
          <Button variant="ghost" size="sm" onClick={() => toIssues('all')}>
            详情
            <ArrowRight size={12} />
          </Button>
        }
      >
        <div className="space-y-4">
          {demo.issues.slice(0, 4).map((issue, index) => (
            <Button
              key={issue.id}
              variant="ghost"
              className="h-auto w-full flex-col items-stretch gap-2 p-0 text-left hover:bg-transparent"
              onClick={() => {
                demo.navigate('issues');
                demo.setSelectedIssue(issue.id);
              }}
            >
              <span className="flex items-center justify-between gap-3">
                <span className="truncate text-[11px] font-normal">
                  <span className="mr-2 text-muted-foreground">0{index + 1}</span>
                  {issue.category}
                </span>
                <span className="text-[11px] tabular-nums">
                  {issue.affectedRuns}
                  <span className="ml-1 font-normal text-muted-foreground">次</span>
                </span>
              </span>
              <span className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <span
                  className={`block h-full rounded-full ${index === 0 ? 'bg-destructive/70' : 'bg-primary/65'}`}
                  style={{
                    width: `${Math.max(
                      12,
                      (issue.affectedRuns / Math.max(...demo.issues.map((i) => i.affectedRuns))) * 100,
                    )}%`,
                  }}
                />
              </span>
            </Button>
          ))}
        </div>
      </Panel>
    </div>
  );
}
