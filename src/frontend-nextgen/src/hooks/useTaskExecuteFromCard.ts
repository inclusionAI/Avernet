/**
 * useTaskExecuteFromCard —— 卡片「执行」按钮拦截入口。
 *
 * 卡片 task_ready 点「执行」→ aixBridge.submit('执行任务', {__taskAction:'execute', task})
 * → chatBridge.ts 拦截层调本 hook 注入的 setTaskExecuteHandler(task)
 * → task JSON → TaskComposerForm → executeTaskService（补 taskComposerContext）
 * → 成功拿 task_id → submitPanelMessage 发 <AixUI type="panel" component="taskPanel.TaskLoopView"> 给 bot
 * → 本地插入 user 消息（SDK 自动）→ MarkdownRender 解析 → 副屏弹出
 * → 消息发后端落库 → loadHistory 拉回 → 副屏持久（切会话/刷新可恢复）。
 */
import type { TaskComposerContext, TaskComposerForm } from '@/services/tasks/taskMapper';
import { buildTaskPanelAixUI } from '@/services/tasks/taskPanelMessage';
import { executeTaskService } from '@/services/tasks/taskService';
import { setTaskExecuteHandler } from '@/services/workspace/chatBridge';
import type { PanelHandle } from '@tc-chat/core';
import { useEffect } from 'react';
import { toast } from 'sonner';

/** task_ready 卡片传出的 task JSON（dataSource.task）结构。 */
interface TaskReadyTask {
  task_type?: 'dynamic' | 'workflow';
  goal?: string;
  deliverables?: string[];
  acceptance_criteria?: string[];
  constraints?: string[];
  resources?: string[];
  workflow_id?: string;
}

export interface UseTaskExecuteFromCardOptions {
  panelRef: React.RefObject<PanelHandle | null>;
  context: TaskComposerContext | null;
  /** 按当前会话直发副屏 <AixUI> 消息（绕开全局桥 last-wins）。由各 pane 注入。 */
  submitPanelMessage: (content: string) => void;
}

export function useTaskExecuteFromCard({ context, submitPanelMessage }: UseTaskExecuteFromCardOptions): void {
  useEffect(() => {
    const handler = (taskRaw: Record<string, unknown>) => {
      const task = taskRaw as TaskReadyTask;
      if (!context) {
        toast.info('当前无可用会话，无法执行任务');
        return;
      }
      // task JSON → TaskComposerForm
      const form: TaskComposerForm = {
        title: (task.goal ?? '').slice(0, 80) || '任务执行',
        objective: task.goal ?? '',
        instruction: [
          task.goal ? `目标：${task.goal}` : '',
          task.deliverables?.length ? `交付物：${task.deliverables.join('；')}` : '',
          task.acceptance_criteria?.length ? `验收标准：${task.acceptance_criteria.join('；')}` : '',
          task.constraints?.length ? `约束：${task.constraints.join('；')}` : '',
        ]
          .filter(Boolean)
          .join('\n'),
        acceptances: task.acceptance_criteria ?? [],
        taskType: task.task_type === 'workflow' ? 'workflow' : 'dynamic',
        workflowId: task.workflow_id,
        background: task.goal ?? '',
      };
      void executeTaskService({ form, ctx: context })
        .then((record) => {
          // 发 <AixUI> 副屏消息给 bot：走正常对话链路（插 user 消息 + 发后端落库）。
          // SDK MarkdownRender 解析 user 消息中的 <AixUI type="panel"> → 副屏弹出。
          // 消息落库 → loadHistory 拉回 → 副屏持久（切会话/刷新可恢复）。
          submitPanelMessage(
            buildTaskPanelAixUI(record.task_id, form.title, {
              taskId: record.task_id,
              apiBaseUrl: '',
              bcsBaseUrl: '',
              userId: context.ownerUserId,
            }),
          );
          toast.success('任务已提交，副屏已打开');
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : '任务提交失败';
          toast.error(msg);
        });
    };
    setTaskExecuteHandler(handler);
    return () => setTaskExecuteHandler(null);
  }, [context, submitPanelMessage]);
}
