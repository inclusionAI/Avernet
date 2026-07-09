// Cron wire-format types.
//
// The shapes here match what `engines/openclaw/cron.py::OpenClawCron` already
// knows how to translate into OCB's snake_case `CronJob` / `CronRunRecord` /
// `CronStatus`. Keeping them bit-compatible means the new `AiCodingCronPlugin`
// on OCB can be a near-copy of `OpenClawCron` with just the RPC names
// re-pointed — no extra translation layer.
//
// Conventions:
//   * Time fields are epoch-ms (`*AtMs`), matching openclaw.
//   * Enum-like strings are lowercase snake_case values but fields are camelCase.
//   * `schedule.kind` is `once` / `every` / `cron` (openclaw parity; OCB's
//     internal `at` schedule maps to `once`, `every` is identical).
//   * `payload.kind` defaults to `agentTurn` with a `message` field — the only
//     shape the relay knows how to fire (via `startChatRun`). `systemEvent`
//     payloads are persisted verbatim but currently skipped at fire time with
//     a `skipped` run record.

export type CronScheduleOnce = {
  kind: 'once';
  /** Absolute epoch-ms to fire. Ignored once fired. */
  atMs: number;
  /** IANA tz name. Informational; relay fires on wall-clock UTC. */
  tz?: string;
};

export type CronScheduleEvery = {
  kind: 'every';
  /** Interval in ms. Fires every `everyMs` ms starting from `firstRunAtMs`. */
  everyMs: number;
  /** First fire time. Defaults to `Date.now() + everyMs` when absent. */
  firstRunAtMs?: number;
  tz?: string;
};

export type CronScheduleCron = {
  kind: 'cron';
  /** Standard 5-field cron expression: `m h dom mon dow`. */
  expr: string;
  /** IANA tz name (e.g. `Asia/Shanghai`). Defaults to UTC. */
  tz?: string;
};

export type CronSchedule = CronScheduleOnce | CronScheduleEvery | CronScheduleCron;

/** The only payload shape the relay knows how to fire. */
export type CronPayloadAgentTurn = {
  kind: 'agentTurn';
  /** User message text handed to Claude. */
  message: string;
  /** Optional per-task model override (overrides relay default). */
  model?: string;
  /** `plan` or `execute`. Defaults to bridge default. */
  permissionMode?: string;
  /** Soft timeout in seconds. Defaults to 24h. */
  timeoutSeconds?: number;
};

/** Relay persists but does not fire non-agentTurn payloads. */
export type CronPayloadOpaque = {
  kind: string;
  [k: string]: unknown;
};

export type CronPayload = CronPayloadAgentTurn | CronPayloadOpaque;

export type CronDelivery = {
  /** `none` = no push delivery (polling mode). `webhook` reserved for future. */
  mode: 'none' | 'webhook';
  to?: string;
  /**
   * Free-form channel id. Used by openclaw to encode user lists and a
   * `__disabled__...` / `__empty__` marker for notify semantics. Relay
   * stores and returns it verbatim.
   */
  accountId?: string;
};

export type CronJobRecord = {
  id: string;
  name: string;
  enabled: boolean;
  schedule: CronSchedule;
  payload: CronPayload;
  /** `isolated` (fresh session per run) or `persistent` (reuse sessionKey). */
  sessionTarget: 'isolated' | 'persistent' | string;
  /**
   * Reserved for session binding. Relay populates this with the last
   * gateway session key used to fire the job when `sessionTarget` is
   * `persistent`.
   */
  boundSessionKey?: string;
  delivery?: CronDelivery;
  /** Bookkeeping (running_at_ms, last_error, etc.). */
  state: Record<string, unknown>;
  createdAtMs: number;
  updatedAtMs: number;
};

export type CronRunRecord = {
  jobId: string;
  runId: string;
  /** Fire start time (epoch-ms). Exposed as `runAtMs` for openclaw parity. */
  runAtMs: number;
  /** Finish time (epoch-ms). Exposed as `ts` for openclaw parity. */
  ts: number;
  status: 'ok' | 'error' | 'skipped';
  error?: string;
  durationMs: number;
  /** Short text summary of the assistant's final reply. */
  summary?: string;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
  };
};

export type CronStatusPayload = {
  running: boolean;
  jobCount: number;
  enabledCount: number;
  /** Next fire time across all enabled jobs (epoch-ms). Null if no jobs. */
  nextRunAtMs: number | null;
};

// ---- Request shapes ----

export type CronAddRequest = {
  name: string;
  schedule: CronSchedule;
  payload: CronPayload;
  sessionTarget?: 'isolated' | 'persistent' | string;
  enabled?: boolean;
  delivery?: CronDelivery;
};

export type CronUpdatePatch = Partial<{
  name: string;
  schedule: CronSchedule;
  payload: CronPayload;
  enabled: boolean;
  delivery: CronDelivery;
  sessionTarget: 'isolated' | 'persistent' | string;
}>;

export type CronUpdateRequest = {
  id: string;
  patch: CronUpdatePatch;
};

export type CronListRequest = {
  includeDisabled?: boolean;
};

export type CronRunRequest = {
  id: string;
  /** `due` = only fire if currently due; `force` = fire regardless. */
  mode?: 'due' | 'force';
};

export type CronRunsRequest = {
  id: string;
  limit?: number;
};
