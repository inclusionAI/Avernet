import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { targetParams, type ManagedRoutine } from '@/services/botScheduledTasks/types';
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
  operate: (work: () => Promise<unknown>, message: string) => Promise<void>;
}) {
  const context = state.context;
  if (!context?.can_manage) return null;
  let targetValid = true;
  try {
    targetParams(context, task, state.identity.userId || '');
  } catch {
    targetValid = false;
  }
  const disabled = !state.editable || state.busy || !targetValid;
  const available =
    context.environments.find((item) => item.runtime_stage === (task.runtime_stage || 'draft'))?.available === true;
  return (
    <div className="flex flex-wrap justify-end gap-2" onClick={(event) => event.stopPropagation()}>
      <ConfirmDialog
        title={`立即执行「${task.name}」`}
        description="将在任务指定的运行环境触发一次执行。"
        confirmText="立即执行"
        disabled={disabled || !task.enabled || !available}
        onConfirm={() => operate(() => state.run(task), '已触发执行，请刷新历史记录查看结果')}
      >
        <Button size="sm" variant="outline" disabled={disabled || !task.enabled || !available}>
          立即执行
        </Button>
      </ConfirmDialog>
      <ConfirmDialog
        title={`${task.enabled ? '停用' : '启用'}「${task.name}」`}
        description="此操作会变更后续自动调度。"
        disabled={disabled}
        onConfirm={() => operate(() => state.toggle(task), task.enabled ? '任务已停用' : '任务已启用')}
      >
        <Button size="sm" variant="outline" disabled={disabled}>
          {task.enabled ? '停用' : '启用'}
        </Button>
      </ConfirmDialog>
      <Button size="sm" variant="outline" disabled={disabled} onClick={() => onEdit(task)}>
        编辑
      </Button>
      <ConfirmDialog
        title="删除定时任务"
        description={`确认删除「${task.name}」？删除后无法恢复。`}
        confirmVariant="destructive"
        disabled={disabled}
        onConfirm={() =>
          operate(async () => {
            await state.remove(task);
            onDeleted(task);
          }, '任务已删除')
        }
      >
        <Button size="sm" variant="outline" disabled={disabled}>
          删除
        </Button>
      </ConfirmDialog>
    </div>
  );
}
