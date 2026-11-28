import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { callbackCompensationReason } from '@/domain/taskEscortParity/callbackCompensationErrors';
import type { CallbackCompensationItemState } from '@/domain/taskEscortParity/callbackCompensationTypes';
import type { CallbackCompensationState } from '@/hooks/taskEscortParity/callbackCompensation/useCallbackCompensation';

const states: Record<CallbackCompensationItemState, string> = {
  queued: '等待发送',
  sending: '发送中',
  received: 'HTTP 已接收',
  failed: '发送失败',
  unknown: '结果未知（不可重发）',
  skipped: '已跳过',
};
const statuses = { queued: '等待执行', running: '执行中', stopped: '已停止', completed: '已完成' };
export function CallbackCompensationBatch({ state }: { state: CallbackCompensationState }) {
  const { batch, batchId, error, storageWarning, busy, canStop, stop, refresh } = state.batch;
  return (
    <div className="space-y-4">
      <section className="space-y-4 rounded-xl border border-border bg-card p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[10px] tracking-widest text-muted-foreground">COMPENSATION BATCH</p>
            <h3 className="mt-1 font-medium">
              补偿批次 {batch && <Badge tone="primary">{statuses[batch.status]}</Badge>}
            </h3>
            <p className="mt-2 break-all font-mono text-xs text-muted-foreground">{batchId}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={!state.canStartNew} onClick={state.startNew}>
              新建补偿
            </Button>
            <Button variant="outline" size="sm" loading={busy === 'read'} disabled={!!busy} onClick={refresh}>
              刷新批次
            </Button>
            <Button
              variant="outline"
              size="sm"
              loading={busy === 'stop'}
              disabled={!canStop || busy === 'stop'}
              onClick={stop}
            >
              停止未发送项
            </Button>
          </div>
        </div>
        <p className="text-xs leading-6 text-muted-foreground">
          关闭窗口不取消批次。停止只影响尚未发送的 queued 项；发送中与结果未知项不会重发。HTTP 已接收不代表业务成功。
        </p>
        <p className="text-xs leading-6 text-muted-foreground">
          批次结束且发送和审计均已处理后，可显式新建补偿。仅清除本页当前批次指针，服务端记录保留；新范围必须重新预览和确认，已接收及结果未知项不会重新获得投递资格。
        </p>
        {!batch && !error && (
          <p role="status" className="text-xs text-muted-foreground">
            正在验证并恢复同一批次…
          </p>
        )}
        {batch && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {(Object.keys(states) as CallbackCompensationItemState[]).map((key) => (
              <div key={key} className="rounded-lg bg-muted p-3">
                <strong className="text-xl tabular-nums">{batch.counts[key] ?? '—'}</strong>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {key === 'received' ? 'HTTP 接收数' : key === 'unknown' ? '结果未知' : states[key]}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>
      {error && (
        <p
          role="alert"
          className="rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-xs text-destructive"
        >
          {error}
        </p>
      )}
      {storageWarning && (
        <p role="status" className="text-xs text-warning">
          {storageWarning}
        </p>
      )}
      {batch && (
        <div className="overflow-x-auto rounded-xl border border-border bg-card">
          <table className="w-full min-w-[650px] text-left text-xs">
            <thead className="bg-muted text-muted-foreground">
              <tr>
                <th className="p-3 font-medium">运行 ID</th>
                <th className="p-3 font-medium">投递结果</th>
                <th className="p-3 font-medium">HTTP</th>
                <th className="p-3 font-medium">说明 / 审计</th>
              </tr>
            </thead>
            <tbody>
              {batch.items.map((item) => (
                <tr key={item.itemId} className="border-t border-border">
                  <td className="max-w-64 break-all p-3 align-top font-mono">{item.flowId}</td>
                  <td className="p-3 align-top">
                    <Badge
                      tone={
                        item.state === 'received'
                          ? 'success'
                          : item.state === 'unknown'
                          ? 'warning'
                          : item.state === 'failed'
                          ? 'error'
                          : 'neutral'
                      }
                    >
                      {states[item.state]}
                    </Badge>
                  </td>
                  <td className="p-3 align-top font-mono">{item.httpStatus ?? '—'}</td>
                  <td className="max-w-lg space-y-1 p-3 align-top text-muted-foreground">
                    {item.reason && <p>{callbackCompensationReason(item.reason)}</p>}
                    {item.auditPending && <p className="text-warning">审计待落库</p>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
