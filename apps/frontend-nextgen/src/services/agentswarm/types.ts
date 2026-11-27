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
 *
 * Every label is exactly three characters so the status column has one fixed
 * width and never reflows between rows; keep new entries to that budget.
 */
export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  RUNNING: '执行中',
  COMPLETED: '已完成',
  FAILED: '已失败',
  CANCELLED: '已取消',
  TIMED_OUT: '已超时',
  GUARD_KILLED: '已终止',
  STUCK: '已卡住',
};

export function runStatusLabel(status: string | null | undefined): string {
  if (!status) return '未知';
  return isRunStatus(status) ? RUN_STATUS_LABELS[status] : status;
}

/** Badge tones the product has, mirrored here so the status map stays pure data. */
export type StatusTone = 'neutral' | 'primary' | 'success' | 'warning' | 'error';

/**
 * One tone per gateway state, so the seven statuses are distinguishable at a
 * glance rather than collapsing into "done" and "not done".
 *
 * - `success` — the run did what was asked.
 * - `primary` — still moving; not an outcome.
 * - `warning` — stopped from outside the run's own logic (a guard cut it short),
 *   which is neither a clean success nor an internal failure.
 * - `error` — failed, timed out, or wedged on its own.
 * - `neutral` — the user stopped it; not a defect.
 */
export const RUN_STATUS_TONES: Record<RunStatus, StatusTone> = {
  RUNNING: 'primary',
  COMPLETED: 'success',
  FAILED: 'error',
  CANCELLED: 'neutral',
  TIMED_OUT: 'error',
  GUARD_KILLED: 'warning',
  STUCK: 'error',
};

/**
 * Tone for a status string. Unknown strings get `neutral` rather than a guess:
 * an unrecognised state should read as "no judgement", not as success.
 */
export function runStatusTone(status: string | null | undefined): StatusTone {
  if (!status) return 'neutral';
  return isRunStatus(status) ? RUN_STATUS_TONES[status] : 'neutral';
}

/**
 * The five states one spawned agent can be in (`AgentStatus` on the gateway).
 *
 * A narrower set than a run's: an agent is queued before it is dispatched and
 * killed when its run is cut short, but it has no cancel, timeout or guard
 * state of its own. Reading these through `runStatusLabel` left QUEUED and
 * KILLED showing their raw gateway strings.
 */
export type AgentStatus = 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'KILLED';

export const AGENT_STATUSES: readonly AgentStatus[] = [
  'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'KILLED',
];

/** Three characters each, matching the run labels' fixed width. */
export const AGENT_STATUS_LABELS: Record<AgentStatus, string> = {
  QUEUED: '排队中',
  RUNNING: '执行中',
  COMPLETED: '已完成',
  FAILED: '已失败',
  KILLED: '已终止',
};

export const AGENT_STATUS_TONES: Record<AgentStatus, StatusTone> = {
  // Waiting is not progress: a muted badge keeps a queue of queued agents
  // visually quiet next to the ones actually working.
  QUEUED: 'neutral',
  RUNNING: 'primary',
  COMPLETED: 'success',
  FAILED: 'error',
  KILLED: 'warning',
};

export function isAgentStatus(value: unknown): value is AgentStatus {
  return typeof value === 'string' && (AGENT_STATUSES as readonly string[]).includes(value);
}

export function agentStatusLabel(status: string | null | undefined): string {
  if (!status) return '未知';
  return isAgentStatus(status) ? AGENT_STATUS_LABELS[status] : status;
}

export function agentStatusTone(status: string | null | undefined): StatusTone {
  if (!status) return 'neutral';
  return isAgentStatus(status) ? AGENT_STATUS_TONES[status] : 'neutral';
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
  /** Required by the gateway: a bot agent provisions a persona, an llm one
   *  answers in-process. Omitted is a 422 rather than a silent llm agent. */
  kind: AgentKind;
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

/** A roster entry that names a stored definition instead of restating it. */
export interface AgentDefinitionRefDTO {
  definition_id: string;
  /**
   * Catalog name to spawn this agent under. Defaults to the definition's own
   * name; the same definition may appear twice under distinct aliases.
   */
  alias?: string | null;
}

export interface SubmitRunDTO {
  tenant: string;
  goal: string;
  /** A run is EITHER a list of inline specs OR a list of definition refs. */
  agents?: AgentSpecDTO[];
  agent_refs?: AgentDefinitionRefDTO[];
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

/**
 * A stored agent definition, as the gateway returns it.
 *
 * `definition_row_id` is the version that was read. An update must send it back:
 * a stale value is refused with 409, which is how two writers avoid silently
 * overwriting each other.
 */
export interface DefinitionDTO {
  definition_id: string;
  name: string;
  role: string;
  instructions: string;
  kind: string;
  model?: string | null;
  engine?: string | null;
  bot_type: string;
  manifest_document: string;
  manifest_digest: string;
  owner_id: string;
  creator_id: string;
  modifier_id: string;
  definition_row_id: number;
  created_at: number;
}

export interface DefinitionListDTO {
  items: DefinitionDTO[];
  total: number;
  page: number;
  page_size: number;
}

/** Fields accepted when creating or revising a definition. */
export interface DefinitionWriteDTO {
  name: string;
  role?: string;
  instructions?: string;
  /** Required by the gateway: a definition's kind decides whether it answers
   *  in-process or provisions a persona. Omitted is a 422, not a default. */
  kind: AgentKind;
  model?: string | null;
  engine?: string | null;
  bot_type?: string | null;
  manifest_document?: string;
}

export interface UpdateDefinitionDTO extends DefinitionWriteDTO {
  definition_id: string;
  definition_row_id: number;
}

export interface QueryDefinitionsDTO {
  name?: string;
  page?: number;
  page_size?: number;
}

export interface RunSummaryDTO {
  run_id: string;
  /** The stored row key — also the submission order. Shown beside run_id. */
  row_id?: number;
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
  /**
   * The catalog entry this spawned agent came from. A spawned agent is named
   * `{catalog_name}_{agent_id[:12]}`, so several agents can share one entry and
   * therefore one definition version.
   */
  catalog_name?: string | null;
  /**
   * The instruction this dispatch was given (`task_spec.objective`). The agent
   * row is created per dispatch, so two spawns of one catalog entry each carry
   * their own — the same entry can be re-dispatched with a different one.
   */
  objective?: string | null;
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
  /**
   * The stored definition this entry came from; null for an inline roster.
   * Reported per catalog entry, not per spawned agent: every spawn of one entry
   * shares the same definition version.
   */
  definition_id?: string | null;
  /** The exact definition version (row id) captured at submit. */
  definition_row_id?: number | null;
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
  /** Allowlisted server-side sort column; unknown values fall back to newest-first. */
  sort?: 'id' | 'run_id' | 'status' | 'started_at' | 'ended_at';
  order?: 'asc' | 'desc';
  /** Keyword search over the run id and the goal text, server-side. */
  keyword?: string;
}