/**
 * 任务执行 Loop Service —— 收口 execute 提交、Store 写入与防重入。
 * 不自动重试 POST（无服务端幂等键），提交期间禁止重复点击。
 *
 * 另收口工作流菜单 Controller 调用（workflow 列表加载 / facade.command 解析），
 * 供 Hook 层使用，避免 Hook 直接 import Controller 违反 TC-G002 分层守卫。
 */
import { executeTask } from '@/services/backendApi/tasks/taskController';
import {
  getWorkflowDetail,
  listWorkflows,
  type WorkflowListItem,
} from '@/services/backendApi/workflows/workflowController';
import { useTaskStore } from '@/stores/taskStore';
import { buildExecuteRequest, unwrapEnvelope, type TaskComposerContext, type TaskComposerForm } from './taskMapper';
import type { TaskRecord } from './taskModel';

export interface ExecuteTaskArgs {
  form: TaskComposerForm;
  ctx: TaskComposerContext;
  /** 可选 API base；空串走当前环境的同源代理。 */
  apiBaseUrl?: string;
}

export async function executeTaskService({ form, ctx, apiBaseUrl }: ExecuteTaskArgs): Promise<TaskRecord> {
  const store = useTaskStore.getState();
  if (store.submitting) {
    throw new Error('任务正在提交中，请稍候');
  }
  store.setSubmitting(true);
  store.setError(null);
  try {
    const req = buildExecuteRequest(form, ctx);
    const resp = unwrapEnvelope(await executeTask(req, apiBaseUrl));
    // 后端 execute 响应精简为 { task_id, success, run_id, message }；副屏只用 task_id。
    const record: TaskRecord = {
      task_id: resp.task_id,
      task_info: {
        task_spec: {
          metadata: { title: form.title.trim(), instruction: form.instruction.trim() },
          context: { background: form.background?.trim() ?? '', extend_props: {} },
          goal: {
            objective: form.objective.trim(),
            acceptances: form.acceptances
              .filter((item) => item.trim())
              .map((description, index) => ({
                id: `ac${index + 1}`,
                description: description.trim(),
              })),
          },
        },
        source_type: ctx.sourceType,
        owner_user_id: ctx.ownerUserId,
        owner_bot_id: ctx.ownerBotId,
        task_type: form.taskType,
        execution_config: {
          task_type: form.taskType,
          ...(form.taskType === 'workflow' && form.workflowId ? { workflow_id: form.workflowId } : {}),
        },
      },
      status: 'EXECUTING',
      create_time: new Date().toISOString(),
      finish_time: null,
    };
    useTaskStore.getState().setLastTask(record);
    return record;
  } catch (err) {
    const msg = err instanceof Error ? err.message : '任务提交失败';
    useTaskStore.getState().setError(msg);
    throw err;
  } finally {
    useTaskStore.getState().setSubmitting(false);
  }
}

/**
 * 加载 workflow 列表（按 ownerUserId + ownerBotId）。
 * 直连 clawweb /api/workflows（见 config 代理）；botOwnerId 透传 ownerUserId，controller 会把
 * ownerBotId（形如 "default:146836"）裁成裸 botId 再下发，避免与 botOwnerId 拼重复。不走 mock。
 */
export async function fetchWorkflows(ownerUserId: string, botId: string): Promise<WorkflowListItem[]> {
  return listWorkflows(ownerUserId, botId);
}

/** 解析工作流触发命令：取 facade.command，失败兜底 workflowId 自身。 */
export async function resolveWorkflowCommand(
  workflowId: string,
  fallback = workflowId,
): Promise<{ command: string; title?: string }> {
  try {
    const detail = await getWorkflowDetail(workflowId);
    return { command: detail.facade?.command ?? fallback, title: detail.title };
  } catch {
    return { command: fallback };
  }
}

export type { WorkflowListItem };
