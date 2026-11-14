import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import type { TodoAcceptanceTarget } from '@/domain/todos/models';
import { Check, Flag } from 'lucide-react';

interface TodoAcceptanceActionsProps {
  loading: boolean;
  rejecting: boolean;
  message: string;
  targets: TodoAcceptanceTarget[];
  onBeginReject: () => void;
  onMessageChange: (message: string) => void;
  onCancelReject: () => void;
  onSubmitReject: () => Promise<void> | void;
  onApprove: () => Promise<void> | void;
}

function selectedFloors(targets: TodoAcceptanceTarget[]): string {
  return [...targets]
    .sort((left, right) => left.floor - right.floor)
    .map((target) => `f${target.floor}`)
    .join('、');
}

export function TodoAcceptanceActions({
  loading,
  rejecting,
  message,
  targets,
  onBeginReject,
  onMessageChange,
  onCancelReject,
  onSubmitReject,
  onApprove,
}: TodoAcceptanceActionsProps) {
  const floors = selectedFloors(targets);

  return (
    <section role="region" aria-label="待办验收" className="rounded-xl border border-warning/45 bg-warning/5 p-4">
      <h2 className="m-0 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Flag className="size-4 shrink-0 text-warning" aria-hidden />
        <span>待你验收 · Bot 已完成处理</span>
      </h2>

      {rejecting ? (
        <div className="mt-3 space-y-3">
          <p className="m-0 text-xs text-muted-foreground">
            {floors ? `已选中问题楼层：${floors}` : '请到互动页标记有问题的楼层'}
          </p>
          <Textarea
            value={message}
            onChange={(event) => onMessageChange(event.target.value)}
            placeholder="填写完整返工要求"
            rows={3}
            disabled={loading}
          />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" className="h-8 rounded-lg px-4 text-xs" onClick={onCancelReject} disabled={loading}>
              取消
            </Button>
            <Button
              variant="outline"
              className="h-8 rounded-lg px-4 text-xs"
              onClick={() => void onSubmitReject()}
              loading={loading}
              disabled={!message.trim() || targets.length === 0}
            >
              提交并退回返工
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button className="h-8 rounded-lg px-4 text-xs" variant="outline" onClick={onBeginReject} disabled={loading}>
            验收不通过
          </Button>
          <Button
            className="h-8 rounded-lg bg-foreground px-4 text-xs text-background hover:opacity-85"
            onClick={() => void onApprove()}
            loading={loading}
            leftIcon={<Check className="size-4" aria-hidden />}
          >
            验收通过
          </Button>
        </div>
      )}
    </section>
  );
}
