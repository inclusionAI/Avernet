import { formatDashboardCount, formatDashboardRate } from '@/domain/taskEscortWorkspace/dashboard';
import type { DashboardDailyPoint } from '@/domain/taskEscortWorkspace/dashboardTypes';
import { useId } from 'react';

/** No interpolation across missing rate samples. Axes stay inside the responsive SVG viewBox. */
export function DashboardQualitySvg({ points }: { points: DashboardDailyPoint[] }) {
  const title = useId();
  const description = useId();
  const left = 56,
    right = 570,
    top = 30,
    bottom = 230;
  const x = (index: number) => left + ((index + 0.5) / points.length) * (right - left);
  const y = (rate: number) => bottom - rate * (bottom - top);
  const maxFailures = Math.max(
    1,
    ...points.flatMap((point) => (point.failedCount === null ? [] : [point.failedCount])),
  );
  const barWidth = Math.min(18, ((right - left) / points.length) * 0.45);
  const segments: Array<Array<{ x: number; y: number }>> = [];
  let current: Array<{ x: number; y: number }> = [];
  points.forEach((point, index) => {
    if (
      point.successRate === null ||
      point.successRate === undefined ||
      !Number.isFinite(point.successRate) ||
      point.successRate < 0 ||
      point.successRate > 1
    ) {
      if (current.length) segments.push(current);
      current = [];
    } else current.push({ x: x(index), y: y(point.successRate) });
  });
  if (current.length) segments.push(current);
  const labelEvery = Math.max(1, Math.ceil(points.length / 5));
  return (
    <svg
      role="img"
      aria-labelledby={title}
      aria-describedby={description}
      viewBox="0 0 640 280"
      className="block h-auto min-h-48 w-full max-w-full"
    >
      <title id={title}>运行质量趋势：运行成功率与异常结束数</title>
      <desc id={description}>左轴为成功率 0 到 100%，右轴为异常结束数。缺失成功率不连线。</desc>
      {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
        <g key={tick}>
          <line x1={left} x2={right} y1={y(tick)} y2={y(tick)} className="stroke-border" strokeDasharray="3 4" />
          <text x={left - 8} y={y(tick) + 4} textAnchor="end" className="fill-muted-foreground text-xs">
            {tick * 100}%
          </text>
          <text x={right + 8} y={y(tick) + 4} className="fill-muted-foreground text-xs">
            {Math.round(maxFailures * tick).toLocaleString('en-US', { notation: 'compact' })}
          </text>
        </g>
      ))}
      <text x={left} y={15} className="fill-muted-foreground text-xs">
        成功率
      </text>
      <text x={right} y={15} textAnchor="end" className="fill-muted-foreground text-xs">
        异常结束数
      </text>
      {points.map((point, index) => (
        <g key={`${point.date}-${index}`}>
          {point.failedCount !== null && (
            <rect
              x={x(index) - barWidth / 2}
              y={bottom - (point.failedCount / maxFailures) * (bottom - top)}
              width={barWidth}
              height={(point.failedCount / maxFailures) * (bottom - top)}
              rx={3}
              className="fill-current text-destructive/40"
            >
              <title>
                {point.date} 异常结束数：{formatDashboardCount(point.failedCount)}
              </title>
            </rect>
          )}
          {(index % labelEvery === 0 || index === points.length - 1) && (
            <text x={x(index)} y={253} textAnchor="middle" className="fill-muted-foreground text-xs">
              {point.date.slice(5)}
            </text>
          )}
        </g>
      ))}
      {segments.map((segment, index) => {
        const path = segment.map((point, i) => `${i === 0 ? 'M' : 'L'}${point.x},${point.y}`).join(' ');
        return (
          <g key={index}>
            <path
              d={`${path} L${segment[segment.length - 1].x},${bottom} L${segment[0].x},${bottom} Z`}
              className="fill-current text-success/10"
            />
            <path d={path} fill="none" stroke="currentColor" strokeWidth={2.5} className="text-success" />
            {segment.map((point, pointIndex) => (
              <circle key={pointIndex} cx={point.x} cy={point.y} r={3} className="fill-current text-success" />
            ))}
          </g>
        );
      })}
      {points.map((point, index) => (
        <g key={`tip-${index}`}>
          <title>
            {point.date} 运行成功率：{formatDashboardRate(point.successRate)}；异常结束数：
            {formatDashboardCount(point.failedCount)}
          </title>
          <rect
            x={x(index) - (right - left) / points.length / 2}
            y={top}
            width={(right - left) / points.length}
            height={bottom - top}
            fill="transparent"
          />
        </g>
      ))}
    </svg>
  );
}
