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
import type { TaskComposerContext, TaskComposerForm } from '@/services/tasks/taskMapper';
import { buildTaskPanelAixUI } from '@/services/tasks/taskPanelMessage';
import { executeTaskService, resolveWorkflowCommand, type WorkflowListItem } from '@/services/tasks/taskService';
import { useTaskStore } from '@/stores/taskStore';
import type { PanelHandle } from '@tc-chat/core';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';
import { useWorkflowList } from './useWorkflowList';

export interface UseTaskExecutionOptions {
  panelRef: React.RefObject<PanelHandle | null>;
  context: TaskComposerContext | null;
  /** 按当前会话直发副屏 <AixUI> 消息（绕开全局桥 last-wins）。由各 pane 注入。 */
  submitPanelMessage: (content: string) => void;
}

/** 工作流选中态（+ 号 chip + 发送拦截用）。command 来自 facade.command，作为指令 workflow_id 值。 */
export interface WorkflowSelection {
  workflowId: string;
  title: string;
  /** 详情接口 facade.command，发送指令 workflow_id 用此值；未就绪时兜底 workflowId。 */
  command?: string;
}

export interface UseTaskExecutionResult {
  submitting: boolean;
  error: string | null;
  lastTaskId: string | null;
  workflows: WorkflowListItem[];
  workflowsLoading: boolean;
  /** 鼠标进入工作流菜单时按当前 Bot 懒加载工作流列表。 */
  loadWorkflows: () => Promise<void>;
  /** 受控选中态：工作流 / 动态任务（驱动 + 号 chip 与发送拦截）。 */
  selectedWorkflow: WorkflowSelection | null;
  pendingDynamic: boolean;
  selectWorkflow: (w: WorkflowSelection) => void;
  selectDynamic: () => void;
  clearSelection: () => void;
  /** 有选中态时：用输入框指令作为标题/目标兜底直接提交（测试阶段跳过 skill 多轮对齐），成功开副屏。 */
  submitFromComposer: (content: string) => Promise<{ ok: true; taskId: string } | { ok: false; reason: string }>;
  /** 提交任务并打开副屏。form 校验不通过返回明确原因字符串，成功返回 task_id。 */
  submit: (form: TaskComposerForm) => Promise<{ ok: true; taskId: string } | { ok: false; reason: string }>;
  /** 不发请求，仅校验 form 并返回 disabledReason。 */
  validate: (form: TaskComposerForm) => string | null;
}

export function useTaskExecution({
  panelRef,
  context,
  submitPanelMessage,
}: UseTaskExecutionOptions): UseTaskExecutionResult {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastTaskId = useTaskStore((s) => s.lastTaskId);
  const { workflows, workflowsLoading, loadWorkflows } = useWorkflowList(context);

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
        submitPanelMessage(
          buildTaskPanelAixUI(record.task_id, form.title, {
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
          }),
        );
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
    [context, panelRef, submitPanelMessage, submitting, validate],
  );

  // ── 受控选中态（+ 号 chip / 发送拦截）──
  const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowSelection | null>(null);
  const [pendingDynamic, setPendingDynamic] = useState(false);
  const selectWorkflow = useCallback((w: WorkflowSelection) => {
    setSelectedWorkflow(w);
    setPendingDynamic(false);
    // 异步取 facade.command 回填，发送指令 workflow_id 用 command（未就绪兜底 workflowId）。
    void resolveWorkflowCommand(w.workflowId).then((res) => {
      setSelectedWorkflow((prev) =>
        prev && prev.workflowId === w.workflowId
          ? { ...prev, command: res.command, title: res.title ?? prev.title }
          : prev,
      );
    });
  }, []);
  const selectDynamic = useCallback(() => {
    setPendingDynamic(true);
    setSelectedWorkflow(null);
  }, []);
  const clearSelection = useCallback(() => {
    setSelectedWorkflow(null);
    setPendingDynamic(false);
  }, []);

  // 有选中态时从输入框指令直接提交（测试阶段跳过 skill 多轮对齐，用指令兜底标题/目标）。
  const submitFromComposer = useCallback(
    async (content: string) => {
      const text = content.trim();
      if (!text) return { ok: false, reason: '请输入任务指令' } as const;
      let form: TaskComposerForm;
      if (selectedWorkflow) {
        form = {
          title: text.slice(0, 60) || selectedWorkflow.title,
          objective: text || selectedWorkflow.title,
          instruction: text,
          acceptances: [],
          taskType: 'workflow',
          workflowId: selectedWorkflow.workflowId,
        };
      } else if (pendingDynamic) {
        form = {
          title: text.slice(0, 60) || '动态任务',
          objective: text,
          instruction: text,
          acceptances: [],
          taskType: 'dynamic',
        };
      } else {
        return { ok: false, reason: '未选中任务类型' } as const;
      }
      const res = await submit(form);
      if (res.ok) clearSelection();
      return res;
    },
    [selectedWorkflow, pendingDynamic, submit, clearSelection],
  );

  return {
    submitting,
    error,
    lastTaskId,
    workflows,
    workflowsLoading,
    loadWorkflows,
    selectedWorkflow,
    pendingDynamic,
    selectWorkflow,
    selectDynamic,
    clearSelection,
    submitFromComposer,
    submit,
    validate,
  };
}
