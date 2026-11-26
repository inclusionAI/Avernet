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
  listExecutableWorkflows,
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
    const isRelay = resp.extend_props?.orchestration_mode === 'relay';
    const rootNodeId = resp.extend_props?.root_node_id;
    const rootExecutionInstruction = resp.extend_props?.root_execution_instruction;
    const record: TaskRecord = {
      task_id: resp.task_id,
      task_info: {
        task_spec: {
          context: {
            title: req.task_spec.context.title,
            background: req.task_spec.context.background,
            extend_props: { ...req.task_spec.context.extend_props },
          },
          goal: {
            objective: req.task_spec.goal.objective,
            acceptances: req.task_spec.goal.acceptances.map((item) => ({
              id: item.id,
              description: item.acceptance,
            })),
          },
        },
        source_type: req.source_type,
        owner_user_id: req.owner_user_id,
        owner_bot_id: req.owner_bot_id,
        task_type: req.execution_config.task_type,
        execution_config: {
          ...req.execution_config,
          ...(isRelay
            ? {
                orchestration_mode: 'relay' as const,
                root_node_id: rootNodeId ?? resp.task_id,
                root_execution_instruction:
                  typeof rootExecutionInstruction === 'string' ? rootExecutionInstruction : undefined,
              }
            : {}),
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
 * 加载当前认证用户可执行的 workflow 列表，不回退到 view/all/mock。
 * ownerUserId 是任务请求者，不一定是协作 Bot 的资产所有者；与 ChatBotView 的复合 ID 约定一致，
 * 优先从 botId:ownerId 拆出真实资产 owner，裸 Bot ID 才沿用传入 owner。此范围不改变认证或任务上下文。
 */
export async function fetchWorkflows(ownerUserId: string, botId: string): Promise<WorkflowListItem[]> {
  const requesterId = ownerUserId.trim();
  const scopedBotId = botId.trim();
  const separator = scopedBotId.indexOf(':');
  const bareBotId = (separator < 0 ? scopedBotId : scopedBotId.slice(0, separator)).trim();
  const botOwnerId = separator < 0 ? requesterId : scopedBotId.slice(separator + 1).trim();
  if (!requesterId || !bareBotId || !botOwnerId) throw new Error('缺少 Bot 或所有者，无法加载可执行工作流');
  return listExecutableWorkflows(botOwnerId, bareBotId);
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
