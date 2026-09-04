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
  // 后端契约 TaskInfoRequestDTO（task API execute 端点，前缀由 capability getTaskApiBase 注入）：
  // - task_spec.metadata 不含 task_id（后端自动生成 UUID）；
  // - task_type 进 execution_config.task_type；来源渠道语义由 source_type + owner_bot_id 承载
  //   （source_type 即原 source_channel_type，owner_bot_id 即原 source_channel_id），不再透传旧字段。
  return {
    task_spec: {
      metadata: { title: form.title.trim(), instruction: form.instruction.trim() },
      context: {
        background: form.background?.trim() ?? '',
        // 会话/群/父任务上下文下沉 execution_config(扁平);建群任务此处为空。
        extend_props: {},
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
      // 会话/群/父任务上下文扁平放入 execution_config(新规范;历史记录读 teamclaw_context 兼容)。
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
