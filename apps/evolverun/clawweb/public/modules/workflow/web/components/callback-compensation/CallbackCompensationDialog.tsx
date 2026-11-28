import { Badge } from './primitives';
import { Button } from './primitives';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from './primitives';
import { callbackCompensationReason } from '../../models/callbackCompensationErrors';
import type { CallbackCompensationCapabilities } from '../../models/callbackCompensationTypes';
import type { CallbackCompensationContext } from '../../models/callbackCompensationTypes';
import type { CallbackCompensationState } from '../../hooks/callback-compensation/useCallbackCompensation';
import { BellRing } from './primitives';
import { CallbackCompensationBatch } from './CallbackCompensationBatch';
import { CallbackCompensationConfirm } from './CallbackCompensationConfirm';
import { CallbackCompensationPreview } from './CallbackCompensationPreview';
import { CallbackCompensationScope } from './CallbackCompensationScope';

export function CallbackCompensationDialog({
  state,
  context,
  capabilities,
}: {
  state: CallbackCompensationState;
  context: CallbackCompensationContext;
  capabilities: CallbackCompensationCapabilities;
}) {
  const stage = state.batch.batchId || state.pending ? 2 : state.preview ? 1 : 0;
  return (
    <Modal open={state.open} onOpenChange={state.setOpen}>
      <ModalContent
        size="full"
        closeLabel="关闭补偿窗口"
        className="flex max-h-[calc(100dvh-2rem)] max-w-[1120px] flex-col gap-0 overflow-hidden rounded-2xl p-0"
      >
        <ModalHeader className="shrink-0 border-b border-border px-5 pb-5 pt-6 sm:px-7">
          <div className="flex flex-wrap items-center gap-2">
            <BellRing aria-hidden className="size-5 text-primary" />
            <ModalTitle className="text-lg">成功回调补偿</ModalTitle>
            <Badge tone="primary">{capabilities.environment}</Badge>
          </div>
          <ModalDescription className="break-all leading-6">
            {context.title ?? context.workflowId} · {context.workflowId} ·
            真实服务端预览与审计，通知补偿不重跑任务
          </ModalDescription>
          <ol className="!mt-5 flex gap-3" aria-label="补偿步骤">
            {['选择范围', '预览与确认', '执行结果'].map((label, index) => (
              <li
                key={label}
                aria-current={stage === index ? 'step' : undefined}
                className={`flex flex-1 items-center gap-2 border-t-2 pt-3 text-xs ${
                  stage >= index ? 'border-primary text-primary' : 'border-border text-muted-foreground'
                }`}
              >
                <span
                  className={`grid size-6 shrink-0 place-items-center rounded-full ${
                    stage === index ? 'bg-primary text-primary-foreground' : 'bg-muted'
                  }`}
                >
                  {index + 1}
                </span>
                {label}
              </li>
            ))}
          </ol>
        </ModalHeader>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto bg-muted/20 p-4 sm:p-6">
          {!state.executionReady && (
            <div
              role="status"
              className="space-y-1 rounded-lg border border-warning/20 bg-warning/10 p-3 text-xs leading-6 text-warning"
            >
              <p>当前仅可预览，执行前提尚未满足。</p>
              {capabilities.blockedReasons.map((reason, index) => (
                <p key={index}>{callbackCompensationReason(reason)}</p>
              ))}
            </div>
          )}
          {state.error && (
            <p
              role="alert"
              className="rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-xs leading-6 text-destructive"
            >
              {state.error}
            </p>
          )}
          {state.batch.batchId ? (
            <CallbackCompensationBatch state={state} />
          ) : state.pending ? (
            <section className="mx-auto max-w-2xl space-y-4 rounded-xl border border-warning/20 bg-card p-5">
              <h3 className="font-medium">创建结果尚未确认</h3>
              <p className="text-xs leading-6 text-muted-foreground">
                请求可能已被服务端受理。关闭窗口不会清除此请求；只能重试相同 previewId、itemIds、原因和
                requestKey。不会重新预览或自动重发。
              </p>
              <p className="text-xs text-muted-foreground">
                预览 ID：<span className="break-all font-mono">{state.pending.previewId}</span> ·{' '}
                {state.pending.itemIds.length} 项
              </p>
              <Button
                loading={state.busy === 'create'}
                disabled={!!state.busy || !state.executionReady}
                onClick={state.execute}
              >
                使用同一请求重试
              </Button>
            </section>
          ) : state.confirming ? (
            <CallbackCompensationConfirm state={state} />
          ) : state.preview ? (
            <CallbackCompensationPreview state={state} />
          ) : (
            <CallbackCompensationScope state={state} capabilities={capabilities} />
          )}
        </div>
        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border bg-card px-5 py-4 sm:px-7">
          <p className="text-[11px] text-muted-foreground">只保存当前批次 ID，不在浏览器持久化 payload、原因或凭据。</p>
          <Button variant="outline" size="sm" onClick={() => state.setOpen(false)}>
            关闭窗口，保留进度
          </Button>
        </footer>
      </ModalContent>
    </Modal>
  );
}
