import { Button } from './primitives';
import { Textarea } from './primitives';
import type { CallbackCompensationState } from '../../hooks/callback-compensation/useCallbackCompensation';

export function CallbackCompensationConfirm({ state }: { state: CallbackCompensationState }) {
  const preview = state.preview!;
  return (
    <div className="mx-auto max-w-2xl space-y-5 rounded-xl border border-border bg-card p-5">
      <div>
        <p className="text-[10px] tracking-widest text-muted-foreground">FINAL CONFIRMATION</p>
        <h3 className="mt-1 font-medium">确认补发通知</h3>
      </div>
      <section aria-label="二次确认摘要" className="rounded-lg border border-primary/20 bg-primary/5 p-4">
        <dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-3 text-xs">
          <dt className="text-muted-foreground">环境</dt>
          <dd>{preview.environment}</dd>
          <dt className="text-muted-foreground">工作流</dt>
          <dd className="break-all font-mono">{preview.workflowId}</dd>
          <dt className="text-muted-foreground">接收者</dt>
          <dd>{preview.receiver.name}</dd>
          <dt className="text-muted-foreground">接收 Origin</dt>
          <dd className="break-all font-mono">{preview.receiver.targetOrigin}</dd>
          <dt className="text-muted-foreground">逻辑接收者</dt>
          <dd className="break-all font-mono">{preview.receiver.receiverId ?? '服务端未提供'}</dd>
          <dt className="text-muted-foreground">补偿项数</dt>
          <dd>{state.selected.length}</dd>
        </dl>
      </section>
      <label className="block space-y-2 text-xs">
        补偿原因
        <Textarea
          aria-label="补偿原因"
          value={state.reason}
          disabled={!!state.busy}
          placeholder="请输入 10–500 字补偿原因，用于审计"
          onChange={(event) => state.setReason(event.target.value)}
        />
        <span className="text-muted-foreground">{state.reason.trim().length} / 500 字，至少 10 字</span>
      </label>
      <label className="flex items-start gap-2 text-xs leading-6 text-muted-foreground">
        <input
          type="checkbox"
          className="mt-1.5 accent-primary"
          checked={state.acknowledged}
          disabled={!!state.busy}
          onChange={(event) => state.setAcknowledged(event.target.checked)}
        />
        <span>我确认只补发通知，不重新执行工作流；HTTP 已接收不代表业务成功，结果未知项不可重发。</span>
      </label>
      {state.expired && (
        <p role="alert" className="text-xs text-destructive">
          预览已过期，不可创建批次。
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" disabled={!!state.busy} onClick={state.back}>
          返回预览
        </Button>
        <Button disabled={!state.canExecute} onClick={state.execute}>
          确认创建补偿批次
        </Button>
      </div>
    </div>
  );
}
