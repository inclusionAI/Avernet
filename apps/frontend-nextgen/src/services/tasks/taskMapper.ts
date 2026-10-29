/**
 * 任务执行 Loop Mapper（服务层）：构造 execute 请求、解包 Envelope。
 * Core EngineStatus → 产品 TaskStatus 的映射在 src/assets/TaskPanel/taskPanelMapper（副屏消费）。
 */
import { isEnvelopeFailure } from '@/services/backendApi/types';
import type { Envelope, ExecuteTaskRequest } from './taskModel';

export type RelayModeCoverageSmokePhase =
  | 'plan_single_bot'
  | 'plan_dynamic_group'
  | 'plan_bbs'
  | 'normal';

export interface RelayModeCoverageSmokeState {
  version: 3;
  current_phase: RelayModeCoverageSmokePhase;
  planned_modes: Array<'single_bot' | 'coop_group' | 'bbs'>;
}

export interface TaskCardHiddenFlags {
  /** Historical evaluation marker: benchmark_execute already created and launched the task; the frontend only renders the card read-only. */
  evaluation_auto_execute?: true;
  /** Legacy unique delivery id retained for card-format compatibility; no longer used for frontend idempotency. */
  evaluation_auto_execute_id?: string;
  relay_mode_coverage_smoke?: RelayModeCoverageSmokeState;
}

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
  hidden_flags?: TaskCardHiddenFlags;
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

/** 工作流选中态（+ 号 chip + 发送拦截用）。command 来自 facade.command，作为指令 workflow_id 用此值；未就绪时兜底 workflowId。 */
export interface WorkflowSelection {
  workflowId: string;
  title: string;
  /** 详情接口 facade.command，发送指令 workflow_id 用此值；未就绪时兜底 workflowId。 */
  command?: string;
}

/** 输入框胶囊选中项（可叠加）：workflow 按 workflowId 去重、dynamic 全场至多一个。 */
export type ComposerSelection = { kind: 'dynamic' } | { kind: 'workflow'; workflow: WorkflowSelection };

/** 胶囊选中项稳定 key：dynamic 固定为 'dynamic'，workflow 用 workflowId（chip React key / 清除定位共用）。 */
export function composerSelectionKey(selection: ComposerSelection): string {
  return selection.kind === 'dynamic' ? 'dynamic' : selection.workflow.workflowId;
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
          ...(form.hidden_flags?.relay_mode_coverage_smoke
            ? { relay_mode_coverage_smoke: form.hidden_flags.relay_mode_coverage_smoke }
            : {}),
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
 * 构造任务指令消息列表，发给会话由 bot/skill 解析触发任务（多轮对齐补齐标题/目标/验收后开副屏）。
 * 一个任务只能绑一个 workflow_id，故逐胶囊出一条指令、各发起一个任务；输入正文作为每条任务的指令。
 * - 动态任务：/task-loop {指令}
 * - 工作流任务：/task workflow_id='{workflowId}' {指令}
 */
export function buildTaskInstructions(content: string, selections: ComposerSelection[]): string[] {
  const text = content.trim();
  return selections.map((selection) =>
    selection.kind === 'workflow'
      ? `/task workflow_id='${selection.workflow.command ?? selection.workflow.workflowId}' ${text}`
      : `/task-loop ${text}`,
  );
}
