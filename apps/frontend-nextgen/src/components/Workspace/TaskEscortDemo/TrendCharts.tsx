import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import { useState } from 'react';
import { DemoRange, DemoRun } from './model';
import { getTrendBuckets, TrendBucket } from './trendBuckets';

function Trend({
  buckets,
  index,
  onSelect,
  kind,
}: {
  buckets: TrendBucket[];
  index: number;
  onSelect: (index: number) => void;
  kind: 'rate' | 'count';
}) {
  const isRate = kind === 'rate';
  const maximum = isRate ? 100 : Math.max(1, ...buckets.map((bucket) => bucket.count));
  const pointX = (i: number) => 34 + i * (390 / Math.max(1, buckets.length - 1));
  const pointY = (value: number) => 100 - (value / maximum) * 70;
  const selected = buckets[index];
  const value = isRate ? (selected.rate === null ? '无终态' : `${selected.rate}%`) : `${selected.count} 个实例`;
  return (
    <figure className="min-w-0 rounded-xl border border-border bg-card p-3.5">
      <figcaption className="flex items-center justify-between gap-2 text-xs">
        <span className="font-medium">
          {isRate ? '成功率趋势' : '实例趋势'} <span className="font-normal text-muted-foreground">· 模拟</span>
        </span>
        <span className="font-mono text-primary">{value}</span>
      </figcaption>
      <svg
        viewBox="0 0 450 120"
        role="img"
        aria-label={`${isRate ? '成功率' : '实例'}模拟趋势，${selected.label}：${value}`}
        className="mt-2 h-28 w-full overflow-visible"
      >
        {[30, 65, 100].map((y) => (
          <line key={y} x1="28" x2="436" y1={y} y2={y} className="stroke-border" strokeDasharray="3 5" />
        ))}
        <text x="0" y="33" className="fill-muted-foreground text-[9px]">
          {isRate ? '100%' : maximum}
        </text>
        <text x="10" y="103" className="fill-muted-foreground text-[9px]">
          0
        </text>
        <line
          x1={pointX(index)}
          x2={pointX(index)}
          y1="22"
          y2="104"
          className="stroke-primary/30"
          strokeDasharray="3 3"
        />
        {isRate ? (
          <>
            {buckets.map((bucket, i) => {
              const previous = buckets[i - 1];
              return bucket.rate === null || previous === undefined || previous.rate === null ? null : (
                <line
                  key={bucket.label}
                  x1={pointX(i - 1)}
                  x2={pointX(i)}
                  y1={pointY(previous.rate)}
                  y2={pointY(bucket.rate)}
                  className="stroke-primary"
                  strokeWidth="2.5"
                />
              );
            })}
            {buckets.map((bucket, i) =>
              bucket.rate === null ? null : (
                <circle
                  key={bucket.label}
                  cx={pointX(i)}
                  cy={pointY(bucket.rate)}
                  r={i === index ? 5 : 3}
                  className="fill-primary stroke-background"
                  strokeWidth="2"
                />
              ),
            )}
          </>
        ) : (
          buckets.map((bucket, i) => (
            <rect
              key={bucket.label}
              x={pointX(i) - 13}
              y={pointY(bucket.count)}
              width="26"
              height={100 - pointY(bucket.count) || 1}
              rx="4"
              className={i === index ? 'fill-primary' : 'fill-primary/25'}
            />
          ))
        )}
      </svg>
      <div className="flex justify-between gap-1" aria-label={`${isRate ? '成功率' : '实例'}时间分桶`}>
        {buckets.map((bucket, i) => (
          <Button
            key={bucket.label}
            variant="ghost"
            size="sm"
            aria-label={`查看${bucket.label}${isRate ? '成功率' : '实例'}趋势`}
            aria-pressed={i === index}
            onClick={() => onSelect(i)}
            className={cn(
              'h-6 min-w-0 flex-1 px-0 text-[9px] text-muted-foreground',
              i === index && 'bg-primary/5 text-primary',
            )}
          >
            {bucket.label.replace('昨日 ', '')}
          </Button>
        ))}
      </div>
    </figure>
  );
}
export function TrendCharts({ range, runs }: { range: DemoRange; runs: DemoRun[] }) {
  const buckets = getTrendBuckets(range, runs);
  const [chosen, setChosen] = useState<number | null>(null);
  const index = Math.min(chosen ?? buckets.length - 1, buckets.length - 1);
  return (
    <section aria-label="模拟运行趋势">
      <div className="grid gap-3 sm:grid-cols-2">
        <Trend kind="rate" buckets={buckets} index={index} onSelect={setChosen} />
        <Trend kind="count" buckets={buckets} index={index} onSelect={setChosen} />
      </div>
      <p aria-live="polite" className="mt-2 text-[10px] text-muted-foreground">
        已选 {buckets[index].label} · 点击分桶查看模拟数值；无终态时不计算成功率。
      </p>
    </section>
  );
}
