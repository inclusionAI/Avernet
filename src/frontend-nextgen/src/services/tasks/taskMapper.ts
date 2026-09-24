/**
 * 任务执行 Loop Mapper（服务层）：构造 execute 请求、解包 Envelope。
 * Core EngineStatus → 产品 TaskStatus 的映射在 src/assets/TaskPanel/taskPanelMapper（副屏消费）。
 */
import { isEnvelopeFailure } from '@/services/backendApi/types';
import type { Envelope, ExecuteTaskRequest } from './taskModel';

export interface TaskComposerForm {
  title: string;
  instruction: string;
  objective: string;
  acceptances: string[];
  taskType: 'dynamic' | 'workflow';
  workflowId?: string;
  background?: string;
  deliverables?: string[];
  constraints?: string[];
  resources?: string[];
}

export interface TaskComposerContext {
  sourceType: 'bot' | 'coop_group';
  ownerUserId: string;
  ownerBotId: string;
  mainSessionId?: string;
  mainSessionName?: string;
  sourceGroupId?: string;
  parentTaskId?: string | null;
}

export function buildExecuteRequest(form: TaskComposerForm, ctx: TaskComposerContext): ExecuteTaskRequest {
  const deliverables = (form.deliverables ?? []).map((item) => item.trim()).filter(Boolean);
  return {
    task_spec: {
      context: {
        title: form.title.trim(),
        background: form.background?.trim() ?? '',
        extend_props: {
          deliverables,
          constraints: (form.constraints ?? []).map((item) => item.trim()).filter(Boolean),
          resources: (form.resources ?? []).map((item) => item.trim()).filter(Boolean),
        },
      },
      goal: {
        objective: form.objective.trim(),
        acceptances: form.acceptances
          .filter((a) => a.trim())
          .map((a, i) => ({ id: `ac${i + 1}`, acceptance: a.trim() })),
      },
    },
    source_type: ctx.sourceType,
    owner_user_id: ctx.ownerUserId,
    owner_bot_id: ctx.ownerBotId,
    execution_config: {
      task_type: form.taskType,
      ...(form.taskType === 'workflow' && form.workflowId ? { workflow_id: form.workflowId } : {}),
      main_session_id: ctx.mainSessionId,
      main_session_name: ctx.mainSessionName,
      source_group_id: ctx.sourceGroupId,
      parent_task_id: ctx.parentTaskId ?? null,
    },
  };
}

export function unwrapEnvelope<T>(env: Envelope<T>): T {
  if (isEnvelopeFailure(env) || !env.data) {
    throw new Error(env.message || `任务接口错误码 ${env.code}`);
  }
  return env.data;
}

/**
 * 构造任务指令消息，发给会话由 bot/skill 解析触发任务（多轮对齐补齐标题/目标/验收后开副屏）。
 * - 动态任务：/task {指令}
 * - 工作流任务：/task workflow_id='{workflowId}' {指令}
 * - 未选中：原样返回指令文本。
 */
export function buildTaskInstruction(
  content: string,
  selectedWorkflow: { workflowId: string; command?: string } | null,
  pendingDynamic: boolean,
): string {
  const text = content.trim();
  if (selectedWorkflow) {
    const wfId = selectedWorkflow.command ?? selectedWorkflow.workflowId;
    return `/task workflow_id='${wfId}' ${text}`;
  }
  if (pendingDynamic) return `/task ${text}`;
  return text;
}
