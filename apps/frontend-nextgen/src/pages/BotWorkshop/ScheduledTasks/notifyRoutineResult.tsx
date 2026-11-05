import { failureReason, targetLabel } from '@/services/botScheduledTasks/results';
import type { OperationResult } from '@/services/botScheduledTasks/types';
import { toast } from 'sonner';

export function notifyRoutineResult(result: OperationResult) {
  const batch = result.batch;
  const description = batch ? (
    <div className="max-h-56 space-y-2 overflow-y-auto">
      {batch.results.map((row, index) => (
        <p key={index}>
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
        </p>
      ))}
      {batch.failed_targets.map((row, index) => (
        <p key={`failed-${index}`}>
          {targetLabel(row)}：{failureReason(row.reason)}
        </p>
      ))}
    </div>
  ) : undefined;
  toast[result.level](result.message, { description, duration: batch ? 10000 : undefined });
}
