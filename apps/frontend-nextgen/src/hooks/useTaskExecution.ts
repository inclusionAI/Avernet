/**
 * useTaskExecution —— 任务发起编排：选择动态/工作流任务、校验、提交、打开任务副屏（taskPanel.TaskLoopView）。
 * 对齐 spec Task 10：提交期间防重入、disabledReason、execute 成功经 submitPanelMessage 发声明式 <AixUI component="taskPanel.TaskLoopView"> 落库持久。
 *
 * workflow 列表经 taskService.fetchWorkflows(ownerUserId, ownerBotId) 动态加载（ownerBotId 形如 "default:146836"，
 * controller 裁成裸 botId 后连同 botOwnerId(=ownerUserId) 直连 clawweb /api/workflows；替代写死列表）。
 * 提交工作流任务时经 taskService.resolveWorkflowCommand 取 facade.command 作为 execution_config.workflow_id。
 * Controller 调用收口在 service 层，Hook 不直接 import controller（守卫 TC-G002）。
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
import { executeTaskService, resolveWorkflowCommand, type WorkflowListItem } from '@/services/tasks/taskService';
import { botSkillService } from '@/services/workspace/botSkillService';
import { splitBotId } from '@/services/workspace/botSessionService';
import { useTaskStore } from '@/stores/taskStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { PanelHandle } from '@tc-chat/core';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { useWorkflowList } from './useWorkflowList';

export interface UseTaskExecutionOptions {
  panelRef: React.RefObject<PanelHandle | null>;
  context: TaskComposerContext | null;
  /** 按当前会话直发副屏 <AixUI> 消息（绕开全局桥 last-wins）。由各 pane 注入。 */
  submitPanelMessage: (content: string) => void;
  /** relay 首棒触发出口；群聊必须定向到 holder，中心化不会调用。 */
  submitTaskExecutionMessage?: (content: string, holderId: string) => void;
}

export type { ComposerSelection, WorkflowSelection } from '@/services/tasks/taskMapper';

export interface UseTaskExecutionResult {
  submitting: boolean;
  error: string | null;
  /** 当前 Bot/协作群 Driver Bot 是否已安装可用的 task-intent-recognition。 */
  dynamicTaskEnabled: boolean;
  dynamicTaskChecking: boolean;
  lastTaskId: string | null;
  workflows: WorkflowListItem[];
  workflowsLoading: boolean;
  /** 鼠标进入工作流菜单时按当前 Bot 懒加载工作流列表。 */
  loadWorkflows: () => Promise<void>;
  /** 受控选中态（可叠加胶囊）：workflow 按 workflowId 去重、动态任务全场至多一个（驱动输入区 chip 与发送拦截）。 */
  selections: ComposerSelection[];
  selectWorkflow: (w: WorkflowSelection) => void;
  selectDynamic: () => void;
  /** 清除指定胶囊（key 见 composerSelectionKey：'dynamic' | workflowId）。 */
  removeSelection: (key: string) => void;
  clearSelection: () => void;
  /** 提交任务并打开副屏。form 校验不通过返回明确原因字符串，成功返回 task_id。 */
  submit: (form: TaskComposerForm) => Promise<{ ok: true; taskId: string } | { ok: false; reason: string }>;
  /** 不发请求，仅校验 form 并返回 disabledReason。 */
  validate: (form: TaskComposerForm) => string | null;
}

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

  useEffect(() => {
    if (!context?.ownerBotId || !context.ownerUserId) {
      setDynamicTaskEnabled(false);
      setDynamicTaskChecking(false);
      return undefined;
    }
    let cancelled = false;
    setDynamicTaskChecking(true);
    const { realBotId, ownerId } = splitBotId(context.ownerBotId);
    void botSkillService
      .listSkillsForBotId(realBotId, context.ownerUserId, ownerId, 1, 100)
      .then((result) => {
        if (cancelled) return;
        setDynamicTaskEnabled(result.ok && botSkillService.hasTaskIntentSkill(result.data));
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
        if (launchMessage.shouldTriggerBot) {
          (submitTaskExecutionMessage ?? submitPanelMessage)(launchMessage.content, context.ownerBotId);
        } else {
          submitPanelMessage(launchMessage.content);
        }
        // V2 多任务切换(spec §3.2):发起任务即自动切换活跃任务(AixUI 面板消息落库后自动开该任务 tab)
        const taskStore = useTaskSidePanelStore.getState();
        taskStore.setActiveTask(record.task_id);
        taskStore.markTaskPanelOpenIntent(record.task_id);
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
