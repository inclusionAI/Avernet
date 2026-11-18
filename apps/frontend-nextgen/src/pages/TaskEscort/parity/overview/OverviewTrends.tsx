import type { OverviewReadState, OverviewTrendPoint } from '@/domain/taskEscortParity/overviewTypes';
import { useState } from 'react';
import { Empty, OverviewTabs, ReadFeedback } from './OverviewShared';

export function OverviewTrends({ state, days }: { state: OverviewReadState<OverviewTrendPoint[]>; days: string }) {
  const [tab, setTab] = useState<'success' | 'count'>('success');
  const points = state.data ?? [],
    key = tab === 'success' ? 'successRate' : 'totalRuns';
  const max = tab === 'success' ? 100 : Math.max(1, ...points.map((point) => point.totalRuns ?? 0));
  const x = (i: number) => 36 + (i * 600) / Math.max(1, points.length - 1);
  const y = (value: number) => 128 - (value / max) * 108;
  return (
    <section className="tcov-card min-w-0" aria-label="运行趋势">
      <OverviewTabs
        label="趋势类型"
        value={tab}
        options={[
          ['success', '成功率趋势'],
          ['count', '运行实例趋势'],
        ]}
        onChange={setTab}
      />
      <div className="p-4">
        <ReadFeedback state={state} label="运行趋势" />
        {!state.loading &&
          !state.error &&
          (points.length === 0 ? (
            <Empty compact title="暂无实际趋势数据" />
          ) : (
            <>
              <svg
                viewBox="0 0 660 158"
                className="h-40 w-full text-primary"
                role="img"
                aria-label={tab === 'success' ? '实际成功率趋势' : '实际运行实例趋势'}
              >
                {[0, 0.5, 1].map((fraction) => (
                  <g key={fraction}>
                    <line
                      x1="36"
                      x2="636"
                      y1={y(max * fraction)}
                      y2={y(max * fraction)}
                      stroke="hsl(var(--border))"
                      strokeDasharray="3 4"
                    />
                    <text x="0" y={y(max * fraction) + 4} fill="hsl(var(--muted-foreground))" fontSize="10">
                      {Math.round(max * fraction)}
                      {tab === 'success' ? '%' : ''}
                    </text>
                  </g>
                ))}
                {points.map((point, i) => {
                  const value = point[key],
                    previous = points[i - 1]?.[key];
                  return (
                    <g key={point.date}>
                      {i > 0 &&
                        value !== null &&
                        value !== undefined &&
                        previous !== null &&
                        previous !== undefined && (
                          <line
                            x1={x(i - 1)}
                            x2={x(i)}
                            y1={y(previous)}
                            y2={y(value)}
                            stroke="currentColor"
                            strokeWidth="2"
                          />
                        )}
                      {value !== null && value !== undefined && (
                        <circle cx={x(i)} cy={y(value)} r="3" fill="currentColor" />
                      )}
                      {(i === 0 || i === points.length - 1 || points.length <= 7) && (
                        <text x={x(i)} y="150" textAnchor="middle" fill="hsl(var(--muted-foreground))" fontSize="10">
                          {point.date.slice(5)}
                        </text>
                      )}
                    </g>
                  );
                })}
              </svg>
              <details className="mt-2 text-xs text-muted-foreground">
                <summary className="cursor-pointer">查看实际趋势数值</summary>
                <div className="tcov-scroll">
                  <table className="tcov-table">
                    <thead>
                      <tr>
                        <th>日期</th>
                        <th>终态运行</th>
                        <th>成功</th>
                        <th>异常</th>
                        <th>成功率</th>
                      </tr>
                    </thead>
                    <tbody>
                      {points.map((point) => (
                        <tr key={point.date}>
                          <td>{point.date}</td>
                          <td>{point.totalRuns ?? '—'}</td>
                          <td>{point.succeededRuns ?? '—'}</td>
                          <td>{point.failedRuns ?? '—'}</td>
                          <td>
                            {point.successRate === null || point.successRate === undefined
                              ? '—'
                              : `${point.successRate}%`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </>
          ))}
        <p className="mt-3 text-[11px] leading-5 text-muted-foreground">
          {days === 'today' || days === 'yesterday'
            ? '趋势接口仅支持最近 1 天，不支持自然日筛选。'
            : `最近 ${days} 天。`}
          趋势接口仅统计终态运行，不含运行中、等待或排队实例。
        </p>
      </div>
    </section>
  );
}
