import { Button } from '@/components/ui/Button';
import { formatDashboardCount, formatDashboardRate } from '@/domain/taskEscortWorkspace/dashboard';
import type { DashboardDailyPoint } from '@/domain/taskEscortWorkspace/dashboardTypes';
import { useId, useState } from 'react';

/** Keyboard/screen-reader equivalent of the chart's per-day hover values. */
export function DashboardTrendDetails({ points }: { points: DashboardDailyPoint[] }) {
  const [expanded, setExpanded] = useState(false);
  const tableId = useId();
  return (
    <div className="mt-2">
      <Button
        size="sm"
        variant="outline"
        aria-expanded={expanded}
        aria-controls={tableId}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? '收起趋势明细' : '查看趋势明细'}
      </Button>
      {expanded && (
        <div
          className="mt-3 max-h-64 overflow-auto rounded-lg border border-border"
          tabIndex={0}
          role="region"
          aria-label="趋势明细滚动区域"
        >
          <table id={tableId} aria-label="运行质量趋势明细" className="w-full text-left text-xs">
            <thead className="bg-muted/50 text-muted-foreground">
              <tr>
                <th scope="col" className="whitespace-nowrap px-3 py-2 font-medium">
                  日期
                </th>
                <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">
                  运行成功率
                </th>
                <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">
                  异常结束数
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {points.map((point) => (
                <tr key={point.date}>
                  <th scope="row" className="whitespace-nowrap px-3 py-2 font-normal">
                    {point.date}
                  </th>
                  <td className="px-3 py-2 text-right tabular-nums">{formatDashboardRate(point.successRate)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{formatDashboardCount(point.failedCount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
