import { failureReason, targetLabel } from '@/services/botScheduledTasks/results';
import type { OperationResult } from '@/services/botScheduledTasks/types';
export function RoutineOperationResult({ result }: { result?: OperationResult }) {
  if (!result) return null;
  return (
    <section role="status" className="space-y-3 rounded-xl border border-border bg-card p-4 text-sm">
      <p className={result.level === 'error' ? 'm-0 text-destructive' : 'm-0'}>{result.message}</p>
      {result.batch && (
        <ul className="m-0 space-y-2 pl-4">
          {result.batch.results.map((row, index) => (
            <li key={index}>
              {targetLabel(row)}：
              {!row.success
                ? failureReason(row.reason)
                : row.run
                ? row.run.status === 'completed'
                  ? '触发已确认'
                  : row.run.status === 'failed'
                  ? '触发失败'
                  : '触发待确认'
                : row.routine
                ? '配置已更新'
                : '请求已响应'}
            </li>
          ))}
        </ul>
      )}
      {result.batch?.failed_targets.map((row, index) => (
        <p key={`failed-${index}`} className="text-destructive">
          {targetLabel(row)}：{failureReason(row.reason)}
        </p>
      ))}
    </section>
  );
}
