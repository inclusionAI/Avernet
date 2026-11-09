/**
 * 任务发起：动态/工作流选择、校验及声明式 TaskLoopView 消息提交（spec Task 10）。
 * 工作流列表/command 经 taskService 动态解析；Hook 不直接 import controller（TC-G002）。
 * 提交只登记显式 open intent；SDK 真正展示后的焦点由共享 controller 投影。
 */
import { TASK_API_BASE } from '@/services/tasks/taskConfig';
import type {
  ComposerSelection,
  TaskComposerContext,
  TaskComposerForm,
  WorkflowSelection,
} from '@/services/tasks/taskMapper';
import { composerSelectionKey } from '@/services/tasks/taskMapper';
import { buildTaskLaunchMessage, buildTaskPanelAixUI } from '@/services/tasks/taskPanelMessage';
import { executeTaskService, resolveWorkflowCommand } from '@/services/tasks/taskService';
import { botSkillService } from '@/services/workspace/botSkillService';
import { splitBotId } from '@/services/workspace/botSessionService';
import { useTaskStore } from '@/stores/taskStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { PanelHandle } from '@tc-chat/core';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { useWorkflowList } from './useWorkflowList';

function taskSkillCacheKey(context: TaskComposerContext): string {
  return `${context.sourceType}:${context.ownerBotId}:${context.ownerUserId}`;
}

export interface UseTaskExecutionOptions {
  panelRef: React.RefObject<PanelHandle | null>;
  context: TaskComposerContext | null;
  /** 按当前会话直发副屏 <AixUI> 消息（绕开全局桥 last-wins）。由各 pane 注入。 */
  submitPanelMessage: (content: string) => void;
  /** relay 首棒触发出口；群聊必须定向到 holder，中心化不会调用。 */
  submitTaskExecutionMessage?: (content: string, holderId: string) => void;
}

export type { ComposerSelection, WorkflowSelection } from '@/services/tasks/taskMapper';

// 编排结果契约已上移至 src/extensions/contracts/groupTasks.ts（collab-form Task 17 源切片类型完备）：
// collab 保留链（GroupChatComposer 等）不得 type-only 依赖本实现文件。历史导入面经此 re-export 兼容。
import type { UseTaskExecutionResult } from '@/extensions/contracts/groupTasks';
export type { UseTaskExecutionResult };

export function useTaskExecution({
  panelRef,
  context,
  submitPanelMessage,
  submitTaskExecutionMessage,
}: UseTaskExecutionOptions): UseTaskExecutionResult {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastTaskId = useTaskStore((s) => s.lastTaskId);
  const { workflows, workflowsLoading, loadWorkflows } = useWorkflowList(context);
  const [dynamicTaskEnabled, setDynamicTaskEnabled] = useState(false);
  const [dynamicTaskChecking, setDynamicTaskChecking] = useState(Boolean(context));
  const taskSkillCacheRef = useRef(new Map<string, boolean>());

  useEffect(() => {
    if (!context?.ownerBotId || !context.ownerUserId) {
      setDynamicTaskEnabled(false);
      setDynamicTaskChecking(false);
      return undefined;
    }
    const cacheKey = taskSkillCacheKey(context);
    const cachedSkill = taskSkillCacheRef.current.get(cacheKey);
    if (cachedSkill !== undefined) {
      // Same driver Bot + same initiator means the Skill capability is unchanged;
      // switching sessions should not trigger another Skills request.
      setDynamicTaskEnabled(cachedSkill);
      setDynamicTaskChecking(false);
      return undefined;
    }
    let cancelled = false;
    setDynamicTaskChecking(true);
    const { realBotId, ownerId } = splitBotId(context.ownerBotId);
    void botSkillService
      .hasTaskIntentSkillForBot({
        realBotId,
        botUuid: context.ownerBotId,
        ownerId,
        currentUserId: context.ownerUserId,
      })
      .then((result) => {
        if (cancelled) return;
        const enabled = result.ok && result.data;
        taskSkillCacheRef.current.set(cacheKey, enabled);
        setDynamicTaskEnabled(enabled);
      })
      .catch(() => {
        // 技能探测只决定任务入口是否可点；意外失败需安全降级为不可点，
        // 不产生未处理 Promise，也不触发全局错误提示。
        if (cancelled) return;
        taskSkillCacheRef.current.set(cacheKey, false);
        setDynamicTaskEnabled(false);
      })
      .finally(() => {
        if (!cancelled) setDynamicTaskChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [context]);

  const validate = useCallback(
    (form: TaskComposerForm): string | null => {
      if (!context) return '当前无可用会话，无法发起任务';
      if (!context.ownerUserId) return '当前用户身份缺失';
      if (!context.ownerBotId) return 'Owner Bot 缺失';
      if (!form.title.trim()) return '请填写任务标题';
      if (!form.objective.trim()) return '请填写任务目标';
      if (form.taskType === 'workflow' && !form.workflowId) return '工作流任务需选择一个 workflow';
      return null;
    },
    [context],
  );

  const submit = useCallback(
    async (form: TaskComposerForm) => {
      const reason = validate(form);
      if (reason) return { ok: false, reason } as const;
      if (!context) return { ok: false, reason: '当前无可用会话' } as const;
      if (submitting) return { ok: false, reason: '任务正在提交中' } as const;
      setSubmitting(true);
      setError(null);
      try {
        // 工作流任务：先取 facade.command 作为 workflow_id（后端按 command 触发该 workflow）。
        let effectiveWorkflowId = form.workflowId;
        if (form.taskType === 'workflow' && form.workflowId) {
          const resolved = await resolveWorkflowCommand(form.workflowId);
          effectiveWorkflowId = resolved.command;
        }
        const record = await executeTaskService({
          form: { ...form, workflowId: effectiveWorkflowId },
          ctx: context,
          apiBaseUrl: TASK_API_BASE,
        });
        const panelMessage = buildTaskPanelAixUI(record.task_id, form.title, {
          taskId: record.task_id,
          apiBaseUrl: TASK_API_BASE,
          bcsBaseUrl: '',
          userId: context.ownerUserId,
          // dashboard 图 DTO 可能不带任务列表元信息，透传创建时可用字段作为副屏 fallback。
          taskInfoFallback: {
            taskTypeLabel: form.taskType === 'workflow' ? '工作流任务' : '动态任务',
            sourceLabel: context.sourceType === 'coop_group' ? '协作群' : 'Bot 会话',
            ownerBotName: context.ownerBotId,
            createdAt: record.create_time,
            finishedAt: record.finish_time,
          },
        });
        const launchMessage = buildTaskLaunchMessage(panelMessage, record);
        // Explicit intent authorizes the SDK open; only actual presentation may project activeTaskId.
        const taskStore = useTaskSidePanelStore.getState();
        taskStore.markTaskPanelOpenIntent(record.task_id);
        try {
          if (launchMessage.shouldTriggerBot) {
            (submitTaskExecutionMessage ?? submitPanelMessage)(launchMessage.content, context.ownerBotId);
          } else {
            submitPanelMessage(launchMessage.content);
          }
        } catch (error) {
          taskStore.consumeTaskPanelOpenIntent(record.task_id);
          throw error;
        }
        return { ok: true, taskId: record.task_id } as const;
      } catch (err) {
        const msg = err instanceof Error ? err.message : '任务提交失败';
        setError(msg);
        toast.error(msg);
        return { ok: false, reason: msg } as const;
      } finally {
        setSubmitting(false);
      }
    },
    [context, panelRef, submitPanelMessage, submitTaskExecutionMessage, submitting, validate],
  );

  // ── 受控选中态（可叠加 chip / 发送拦截）──
  const [selections, setSelections] = useState<ComposerSelection[]>([]);
  const selectWorkflow = useCallback((w: WorkflowSelection) => {
    // 同一工作流去重：已有该 workflowId 的胶囊时不重复添加。
    setSelections((prev) =>
      prev.some((s) => s.kind === 'workflow' && s.workflow.workflowId === w.workflowId)
        ? prev
        : [...prev, { kind: 'workflow', workflow: w }],
    );
    // 异步取 facade.command 回填，发送指令 workflow_id 用 command（未就绪兜底 workflowId）。
    void resolveWorkflowCommand(w.workflowId).then((res) => {
      setSelections((prev) =>
        prev.map((s) =>
          s.kind === 'workflow' && s.workflow.workflowId === w.workflowId
            ? {
                kind: 'workflow',
                workflow: { ...s.workflow, command: res.command, title: res.title ?? s.workflow.title },
              }
            : s,
        ),
      );
    });
  }, []);
  const selectDynamic = useCallback(() => {
    if (!dynamicTaskEnabled) return;
    // 动态任务全场至多一个：已有则忽略。
    setSelections((prev) => (prev.some((s) => s.kind === 'dynamic') ? prev : [...prev, { kind: 'dynamic' }]));
  }, [dynamicTaskEnabled]);
  const removeSelection = useCallback((key: string) => {
    setSelections((prev) => prev.filter((s) => composerSelectionKey(s) !== key));
  }, []);
  const clearSelection = useCallback(() => {
    setSelections([]);
  }, []);

  return {
    submitting,
    error,
    lastTaskId,
    workflows,
    workflowsLoading,
    loadWorkflows,
    selections,
    selectWorkflow,
    selectDynamic,
    removeSelection,
    clearSelection,
    dynamicTaskEnabled,
    dynamicTaskChecking,
    submit,
    validate,
  };
}
