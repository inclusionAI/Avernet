/**
 * useTaskExecuteFromCard —— 卡片「执行」按钮拦截入口。
 *
 * 卡片 task_ready 点「执行」→ aixBridge.submit('执行任务', {__taskAction:'execute', task})
 * → chatBridge.ts 拦截层调本 hook 注入的 setTaskExecuteHandler(task)
 * → 任务 JSON → TaskComposerForm → 大促 OKR 前置判断 Mock（本地 assistant 回复）→ executeTaskService（补 taskComposerContext）
 * → 成功拿 task_id → submitPanelMessage 发 <AixUI type="panel" component="taskPanel.TaskLoopView"> 给 bot
 * → 本地插入 user 消息（SDK 自动）→ MarkdownRender 解析 → 副屏弹出
 * → 消息发后端落库 → loadHistory 拉回 → 副屏持久（切会话/刷新可恢复）。
 *
 * 执行前门禁：校验当前会话所属 Bot 是否开启「任务认领」（task_claim_mode），
 * 未开启则提示去任务协作页授权并阻断执行（不进入 preflight / execute）。
 */
import { isBotTaskClaimEnabled } from '@/services/tasks/taskClaimQuery';
import type { TaskComposerContext, TaskComposerForm } from '@/services/tasks/taskMapper';
import { buildTaskPanelAixUI } from '@/services/tasks/taskPanelMessage';
import { runTaskPreflightMock } from '@/services/tasks/taskPreflightMock';
import { executeTaskService } from '@/services/tasks/taskService';
import { setTaskExecuteHandler } from '@/services/workspace/chatBridge';
import type { PanelHandle } from '@tc-chat/core';
import { useEffect, useRef } from 'react';
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
  /** 演示用：本地追加 assistant 回复，不通过聊天网络请求。 */
  appendAssistantMessage?: (content: string) => void;
  /** 演示用：以流式方式追加 assistant 回复，不通过聊天网络请求。 */
  streamAssistantMessage?: (content: string) => Promise<void>;
  /** 执行前门禁未通过时，toast「去开启」按钮的跳转回调（跳任务协作页）。无则 toast 无跳转 action。 */
  onOpenCollaborationPermissions?: () => void;
}

export function useTaskExecuteFromCard({
  context,
  submitPanelMessage,
  appendAssistantMessage,
  streamAssistantMessage,
  onOpenCollaborationPermissions,
}: UseTaskExecuteFromCardOptions): void {
  const inFlightRef = useRef(false);

  useEffect(() => {
    const handler = (taskRaw: Record<string, unknown>) => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      const task = taskRaw as TaskReadyTask;
      if (!context) {
        toast.info('当前无可用会话，无法执行任务');
        inFlightRef.current = false;
        return;
      }
      void (async () => {
        try {
          // 执行前门禁：当前会话所属 Bot 未开启任务认领 → 提示去任务协作页授权并阻断执行。
          if (context.ownerBotId) {
            const enabled = await isBotTaskClaimEnabled(context.ownerBotId);
            if (!enabled) {
              toast.warning('当前 Bot 未开启任务认领，请先去任务协作页对当前 Bot 授权开启后再执行', {
                action: onOpenCollaborationPermissions
                  ? { label: '去开启', onClick: () => onOpenCollaborationPermissions() }
                  : undefined,
              });
              return;
            }
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
            // task_ready.task.constraints is the TaskInfo background payload.
            background: task.constraints?.filter((item) => item.trim()).join('；') ?? '',
          };
          // 演示用：命中大促 OKR 时，本地模拟当前 Bot 的需求分析和委派回复。
          // 不发送隐藏指令，不请求专家 Bot；回复完成后仍使用原 context 调用真实 execute。
          const preflight = await runTaskPreflightMock(form);
          if (preflight.matched) {
            if (streamAssistantMessage) {
              await streamAssistantMessage(preflight.message);
            } else {
              appendAssistantMessage?.(preflight.message);
            }
            // 演示脚本：Bot 回复完成后停留 3 秒，再启动真实任务执行。
            await new Promise<void>((resolve) => {
              window.setTimeout(resolve, 3_000);
            });
          }

          const record = await executeTaskService({ form, ctx: context });
          // 发 <AixUI> 副屏消息给 bot：走正常对话链路（插 user 消息 + 发后端落库）。
          // SDK MarkdownRender 解析 user 消息中的 <AixUI type="panel"> → 副屏弹出。
          // 消息落库 → loadHistory 拉回 → 副屏持久（切会话/刷新可恢复）。
          submitPanelMessage(
            buildTaskPanelAixUI(record.task_id, form.title, {
              taskId: record.task_id,
              apiBaseUrl: '',
              bcsBaseUrl: '',
              userId: context.ownerUserId,
              // dashboard 图 DTO 不一定带任务列表中的元信息，先把创建时的可用字段
              // 作为副屏 fallback 透传，轮询到完整字段后由 dashboard 值覆盖。
              taskInfoFallback: {
                taskTypeLabel: form.taskType === 'workflow' ? '工作流任务' : '动态任务',
                sourceLabel: context.sourceType === 'coop_group' ? '协作群' : 'Bot 会话',
                ownerBotName: context.ownerBotId,
                createdAt: record.create_time,
                finishedAt: record.finish_time,
              },
            }),
          );
        } catch (err) {
          const msg = err instanceof Error ? err.message : '任务提交失败';
          toast.error(msg);
        } finally {
          inFlightRef.current = false;
        }
      })();
    };
    setTaskExecuteHandler(handler);
    return () => setTaskExecuteHandler(null);
  }, [appendAssistantMessage, context, streamAssistantMessage, submitPanelMessage, onOpenCollaborationPermissions]);
}
