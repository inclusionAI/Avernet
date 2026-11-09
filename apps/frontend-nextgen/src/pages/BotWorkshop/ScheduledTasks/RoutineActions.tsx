import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { preciseTarget, type ManagedRoutine, type OperationResult } from '@/services/botScheduledTasks/types';
import type { useScheduledTasks } from './useScheduledTasks';
export function RoutineActions({
  task,
  state,
  onEdit,
  onDeleted,
  operate,
}: {
  task: ManagedRoutine;
  state: ReturnType<typeof useScheduledTasks>;
  onEdit: (task: ManagedRoutine) => void;
  onDeleted: (task: ManagedRoutine) => void;
  operate: (work: () => Promise<OperationResult>, message: string) => Promise<void>;
}) {
  const context = state.context;
  if (!context?.can_manage) return null;
  const targetValid = context.bot_type !== 'service' || Boolean(task.runtime_stage);
  const disabled = !state.editable || state.busy || !targetValid;
  const stageOnly =
    context.bot_type === 'service' &&
    Boolean(task.runtime_stage && task.runtime_stage !== 'draft') &&
    !preciseTarget(context, task);
  const contentDisabled = disabled || stageOnly;
  return (
    <div className="flex flex-wrap justify-end gap-2" onClick={(event) => event.stopPropagation()}>
      <ConfirmDialog
        title={`立即执行「${task.name}」`}
        description={
          stageOnly
            ? '此请求仅指定运行态，可能触发该运行态的多个实例。请确认作用范围。'
            : '将在任务指定的运行目标触发一次执行。'
        }
        confirmText="立即执行"
        disabled={disabled || !task.enabled}
        onConfirm={() => operate(() => state.run(task), '已触发执行，请刷新历史记录查看结果')}
      >
        <Button size="sm" variant="outline" disabled={disabled || !task.enabled}>
          立即执行
        </Button>
      </ConfirmDialog>
      <ConfirmDialog
        title={`${task.enabled ? '停用' : '启用'}「${task.name}」`}
        description={stageOnly ? '此请求可能同步该运行态的多个实例，仅修改启停状态。' : '此操作会变更后续自动调度。'}
        disabled={disabled}
        onConfirm={() => operate(() => state.toggle(task), task.enabled ? '任务已停用' : '任务已启用')}
      >
        <Button size="sm" variant="outline" disabled={disabled}>
          {task.enabled ? '停用' : '启用'}
        </Button>
      </ConfirmDialog>
      <Button size="sm" variant="outline" disabled={contentDisabled} onClick={() => onEdit(task)}>
        编辑
      </Button>
      <ConfirmDialog
        title="删除定时任务"
        description={`确认删除「${task.name}」？删除后无法恢复。`}
        confirmVariant="destructive"
        disabled={contentDisabled}
        onConfirm={() =>
          operate(async () => {
            const result = await state.remove(task);
            onDeleted(task);
            return result;
          }, '任务已删除')
        }
      >
        <Button size="sm" variant="outline" disabled={contentDisabled}>
          删除
        </Button>
      </ConfirmDialog>
    </div>
  );
}
