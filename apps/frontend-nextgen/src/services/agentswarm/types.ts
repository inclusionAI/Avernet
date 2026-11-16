/**
 * Wire types for the agentswarm gateway (`/openapi/v1/agentswarm`).
 *
 * These mirror `apps/backend/src/agentclaw/community/adapters/http/agentswarm/schemas.py`
 * and are intentionally a transcription, not a client-side model: the surface owns
 * run truth, so anything invented here would be an invented state.
 */

/** The seven states the gateway can report. There is no authorization state. */
export type RunStatus = 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'TIMED_OUT' | 'GUARD_KILLED' | 'STUCK';

export const RUN_STATUSES: readonly RunStatus[] = [
  'RUNNING',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
  'TIMED_OUT',
  'GUARD_KILLED',
  'STUCK',
];

export const TERMINAL_STATUSES: readonly RunStatus[] = [
  'COMPLETED',
  'FAILED',
  'CANCELLED',
  'TIMED_OUT',
  'GUARD_KILLED',
  'STUCK',
];

export function isRunStatus(value: unknown): value is RunStatus {
  return typeof value === 'string' && (RUN_STATUSES as readonly string[]).includes(value);
}

export function isTerminalStatus(value: unknown): boolean {
  return typeof value === 'string' && (TERMINAL_STATUSES as readonly string[]).includes(value);
}

/**
 * Labels for the seven gateway states. A state outside this map is rendered
 * with the gateway's own string rather than a guessed label.
 */
export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  RUNNING: '执行中',
  COMPLETED: '已完成',
  FAILED: '执行失败',
  CANCELLED: '已取消',
  TIMED_OUT: '执行超时',
  GUARD_KILLED: '已被运行保护机制终止',
  STUCK: '执行无法继续',
};

export function runStatusLabel(status: string | null | undefined): string {
  if (!status) return '未知';
  return isRunStatus(status) ? RUN_STATUS_LABELS[status] : status;
}

/** Agent kinds accepted by the gateway. `bot` provisions a container; `llm` runs in-process. */
export type AgentKind = 'bot' | 'llm';

export type ProviderKind = 'local' | 'remote';

/**
 * `extra="forbid"` on the gateway side: any field not listed here is a 422,
 * so this interface must stay a faithful transcription.
 */
export interface AgentSpecDTO {
  name: string;
  role: string;
  instructions?: string;
  kind?: AgentKind;
  model?: string | null;
  engine?: string | null;
  bot_type?: string | null;
  identity_file?: string | null;
  metadata?: Record<string, string>;
  /** Full five-category manifest YAML, provisioned verbatim when present. */
  config_manifest?: string;
}

/**
 * Operational overrides for one run. Keys mirror the deployment's
 * `agentswarm.orchestration.*` config; omitted values fall back to it.
 * Also `extra="forbid"`.
 */
export interface RunOptionsDTO {
  model_name?: string | null;
  slice_max_seconds?: number | null;
  per_attempt_timeout_seconds?: number | null;
  bot_chat_timeout_seconds?: number | null;
  setup_timeout_seconds?: number | null;
  max_turns?: number | null;
  max_total_agents?: number | null;
  max_width?: number | null;
  max_attempts?: number | null;
}

export interface SubmitRunDTO {
  tenant: string;
  goal: string;
  agents: AgentSpecDTO[];
  provider?: ProviderKind | null;
  model?: string | null;
  idempotency_key?: string | null;
  /**
   * Required by the gateway when any agent is `bot`-kind, despite being optional
   * in the schema: a bot-kind run without it is refused with 400.
   */
  identity_bot_id?: string | null;
  options?: RunOptionsDTO | null;
}

export interface RunSummaryDTO {
  run_id: string;
  status: string;
  termination_reason?: string | null;
  final_answer?: unknown;
  error?: string | null;
  started_at?: number | null;
  ended_at?: number | null;
  /** The goal's prefix; the full goal is on `RunDetailDTO`. */
  goal_excerpt?: string;
}

export interface RunListDTO {
  items: RunSummaryDTO[];
  total: number;
  page: number;
  page_size: number;
}

export interface SubmitRunResponseDTO {
  run_id: string;
}

export interface AgentMessageDetailDTO {
  message_id: string;
  agent_id: string;
  seq: number;
  role: string;
  content?: unknown;
  decision?: unknown;
  token_usage?: unknown;
  artifact_key?: string | null;
  created_at: number;
}

export interface AgentAttemptDetailDTO {
  claim_epoch: number;
  slice_retry_index: number;
  executor_kind: string;
  executor_id: string;
  executor_name: string;
  binding_state: string;
  result_status?: string | null;
  result_message?: string | null;
  started_at?: number | null;
}

export interface AgentResultDetailDTO {
  payload?: unknown;
  artifact_key?: string | null;
  routed_at: number;
}

/** Present on an agent only while its bot sits at the human approval gate. */
export interface BotAuthorizationDTO {
  state: string;
  iframe_url: string;
  redirect_url: string;
  deadline: string;
  application_id: string;
}

export interface AgentDetailDTO {
  agent_id: string;
  name: string;
  role?: string | null;
  kind: string;
  status: string;
  parent_agent_id?: string | null;
  depth: number;
  spawn_round: number;
  idempotency_key: string;
  attempts: number;
  bot_id?: string | null;
  bot_state?: string | null;
  error?: string | null;
  output?: unknown;
  started_at?: number | null;
  ended_at?: number | null;
  result?: AgentResultDetailDTO | null;
  attempt_ledger?: AgentAttemptDetailDTO[];
  messages?: AgentMessageDetailDTO[];
  /**
   * Reported only for bots parked on the human gate. An identity-inheriting
   * run never reaches it, and this surface renders no approval UI.
   */
  authorization?: BotAuthorizationDTO | null;
}

export interface AgentCatalogSpecDTO {
  name: string;
  role?: string | null;
  kind: string;
  model?: string | null;
  engine?: string | null;
}

export interface RoundDetailDTO {
  round_id: string;
  stage_index: number;
  orchestrator_steps: number;
  slowest_attempt_steps: number;
  cohort_agent_ids: string[];
  started_at: number;
  ended_at?: number | null;
}

export interface GuardEventDetailDTO {
  event_id: string;
  agent_id?: string | null;
  kind: string;
  details?: unknown;
  created_at: number;
}

export interface RunDetailDTO extends RunSummaryDTO {
  tenant: string;
  goal: string;
  provider: string;
  agent_catalog?: AgentCatalogSpecDTO[];
  agents?: AgentDetailDTO[];
  rounds?: RoundDetailDTO[];
  guards?: GuardEventDetailDTO[];
}

export interface CancelResponseDTO {
  run_id: string;
  status: string;
}

export interface ListRunsQuery {
  page?: number;
  page_size?: number;
  status?: RunStatus;
}