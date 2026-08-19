/**
 * Configuration type definitions for all ClawFlow capabilities.
 *
 * Each section corresponds to a top-level key in configs/application.yaml.
 * Environment variable overrides are documented per field.
 */
import type { FlowControlConfig } from "../flow-control/types.js";

// ── State Persistence ──

export type StatePersistenceConfig = {
  /** Master switch for state persistence writes. When false, only flow_events are written. */
  enabled: boolean;
  /** Truncation threshold for input_json / output_json fields (KB). Default: 10 */
  maxIoSizeKb: number;
  /** Whether to write to flow_metrics table. Default: true */
  recordMetrics: boolean;
};

// ── Knowledge Injection ──

export type YuQueSourceConfig = {
  /** Enable YuQue as a knowledge source. */
  enabled: boolean;
  /** YuQue API token. Overridden by YUQUE_TOKEN env var. */
  token: string;
  /** YuQue domain (e.g. "yuque.antfin.com"). */
  domain: string;
  /** YuQue namespace scopes to search (e.g. ["team/docs"]). */
  namespaces: string[];
};

export type AgentMindSourceConfig = {
  /** Enable AgentMind as a knowledge source. */
  enabled: boolean;
  /** AgentMind API token. Overridden by AGENTMIND_TOKEN env var. */
  token: string;
  /** AgentMind API endpoint. */
  endpoint: string;
  /** AgentMind knowledge base ID. */
  knowledgeBaseId: string;
};

export type KnowledgeConfig = {
  /** Master switch for knowledge injection. Default: false */
  enabled: boolean;
  /** Per-search timeout in milliseconds. Default: 3000 */
  timeoutMs: number;
  /** Search result cache TTL in milliseconds. Default: 60000 */
  cacheTtlMs: number;
  /** Maximum results per KB query. Default: 5 */
  maxResults: number;
  /** Knowledge source configurations. */
  sources: {
    yuque: YuQueSourceConfig;
    agentmind: AgentMindSourceConfig;
  };
};

// ── Intelligent Retry ──

export type RetryConfig = {
  /** Enable KB-enriched retry on node failure. Default: false */
  kbSearchEnabled: boolean;
  /** Maximum automatic KB-enriched retries per node. Default: 1 */
  maxAutoRetry: number;
  /** Maximum error context entries per flow (FIFO eviction). Default: 20 */
  errorContextMaxEntries: number;
};

// ── Execution Analysis ──

export type ThresholdsConfig = {
  /** Alert if health_score falls below this value. Default: 0.6 */
  healthScore: number;
  /** Alert if tool_failure_rate exceeds this value. Default: 0.3 */
  toolFailureRate: number;
  /** Alert if incomplete_rate exceeds this value. Default: 0.3 */
  incompleteRate: number;
};

export type AnalysisConfig = {
  /** Master switch for post-workflow analysis. Default: false */
  enabled: boolean;
  /** Thresholds for analyzer alerts. */
  thresholds: ThresholdsConfig;
};

// ── Alert Notifications ──

export type DingTalkConfig = {
  /** DingTalk webhook URLs for alert notifications (group robots). */
  webhooks: string[];
  /** Keywords included in DingTalk messages for group filtering. */
  keywords: string[];
};

export type AlertingConfig = {
  /** Master switch for alerting. Default: false */
  enabled: boolean;
  /** Auto-create alert when a node fails (after retries exhausted). Default: true */
  onNodeFailure: boolean;
  /** DingTalk notification configuration. */
  dingtalk: DingTalkConfig;
};

// ── Query API ──

export type ApiConfig = {
  /** Enable the Express query API server. Default: false */
  enabled: boolean;
  /** HTTP port for the API server. Default: 3210 */
  port: number;
  /** Bind host. Default: "127.0.0.1" (localhost only). */
  host: string;
  /** API key for authentication. Overridden by WORKFLOW_API_KEY env var. */
  apiKey: string;
  /** Base URL of the clawweb server for API calls. Overridden by CLAWWEB_URL env var. */
  baseUrl: string;
  /** Base64-encoded PKCS8 DER Ed25519 private key for signing internal API requests. */
  privateKeyB64: string;
  /** IAM token for Cookie-based auth. Overridden by CLAWMIND_IAMTOKEN env var. */
  iamtoken: string;
  /** Request timeout in milliseconds. Default: 5000 */
  timeout: number;
  /** Max retries for transient errors. Default: 3 */
  maxRetries: number;
  /** External-facing URL for approval page links (e.g. DingTalk sidebar).
   *  Falls back to baseUrl if not set. Overridden by CLAWWEB_URL env var. */
  clawwebUrl: string;
  /** DingTalk corp ID for JSAPI auth. Overridden by DINGTALK_CORP_ID env var. */
  corpId: string;
};

// ── Scheduler ──

export type SchedulerConfig = {
  /** Master switch for the cron scheduler. Default: false */
  enabled: boolean;
  /** Poll interval in milliseconds. Default: 60000 (60s) */
  pollIntervalMs: number;
  /** Default max concurrent runs per trigger. Default: 1 */
  defaultMaxConcurrent: number;
  /** Missed fire recovery policy. Default: "fireLast" */
  missedFirePolicy: "skip" | "fireLast" | "fireAll";
};

// ── Webhook ──

export type WebhookConfig = {
  /** Master switch for the webhook system. Default: false */
  enabled: boolean;
  /** Maximum request body size in KB. Default: 1024 (1MB) */
  maxPayloadKb: number;
  /** Idempotency dedup window in hours. Default: 24 */
  idempotencyWindowHours: number;
  /** Days to retain webhook event logs. Default: 30 */
  eventRetentionDays: number;
};

// ── Async Callback ──

export type AsyncCallbackConfig = {
  /** Master switch for the async-callback node system. Default: true */
  enabled: boolean;
  /** Default callback base URL (e.g. "https://clawweb.antgroup-inc.cn").
   *  Nodes can override this with their own `callbackBaseUrl`. */
  callbackBaseUrl: string;
  /** Default timeout for callback tokens if not specified on the node. Default: "1h" */
  defaultTimeout: string;
  /** Polling interval in ms for the callback-timeout-poller. Default: 60000 */
  timeoutPollIntervalMs: number;
  /** Default HMAC secret for callback signature validation when mode is "hmac".
   *  Nodes can override with their own `auth.secret`. */
  defaultHmacSecret: string;
  /** Maximum request body size in KB for callback payloads. Default: 256 */
  maxCallbackPayloadKb: number;
  /** Days to retain consumed/expired callback tokens. Default: 7 */
  tokenRetentionDays: number;
};

// ── Context Compression ──

/** Global default configuration for context compression. */
export type ContextCompressionDefaults = {
  /** Master switch for context compression. Default: false */
  enabled: boolean;
  /** Default max tokens when no per-workflow/node config is set. Default: 8000 */
  defaultMaxTokens: number;
  /** Default warning threshold ratio (0–1). Default: 0.7 */
  warningThresholdRatio: number;
  /** Default overflow strategy. Default: "priority-evict" */
  defaultOverflowStrategy: import("../context/types.js").CompressionStrategy;
  /** Default compression pipeline steps when enabled. */
  defaultSteps: import("../context/types.js").CompressionStep[];
};

// ── Session Compression ──

/** Global configuration for session-level (JSONL) compression. */
export type SessionCompressionAppConfig = {
  /** Whether tool-output-prepass is enabled. Default: true */
  toolPrepassEnabled: boolean;
  /** Maximum chars for a single tool result before truncation. Default: 5000 */
  toolResultMaxChars: number;
  /** Number of recent message pairs to keep unchanged. Default: 6 */
  recencyWindow: number;
  /** Token budget for the compressed session. Default: 50000 */
  maxSessionTokens: number;
  /** Whether to insert a compaction notice system message. Default: true */
  insertCompactionNotice: boolean;
  /** Whether to deduplicate repeated file reads. Default: true */
  deduplicateReads: boolean;
  /** TTL in ms for read dedup cache. Default: 300000 (5 min) */
  readDedupTtlMs: number;
  /** Minimum session tokens to trigger compression. Default: 30000 */
  minTokensToCompact: number;
  /**
   * Target token budget for the session within the context window.
   * Used as the denominator for budgetRatio calculations and to compute
   * effectiveMaxSessionTokens (budget minus non-session overhead).
   * Falls back to maxSessionTokens if unset.
   */
  contextTokenBudget?: number;
  /**
   * The model's actual context window size (tokens).
   * Used for the overhead-dominated check: if non-session overhead (system
   * prompt + tool definitions + skills) alone exceeds this ceiling, session
   * compression is mathematically futile and is skipped early.
   * Should match the model's real limit (e.g., 131072 for Kimi-K2.5,
   * 200000 for Claude Sonnet). Falls back to contextTokenBudget if unset.
   */
  modelContextWindow?: number;
  /**
   * Whether to enable session file compression for the main/sub agent sessions
   * via the before_prompt_build global fallback path.
   *
   * Default: false — main session compression is opt-in to prevent the
   * accidental in-place rewrite (data loss) observed in production. When
   * disabled, only embedded-agent sessions (which register a per-session
   * entry) are compressed.
   *
   * NOTE: This switch is only consulted when `contextCompression.enabled`
   * is true. When `contextCompression.enabled` is false, ALL session file
   * compression is skipped regardless of this flag.
   */
  mainSessionEnabled: boolean;
};

// ── LLM Concurrency Control ──

/** Global LLM concurrency limiting configuration. */
export type LlmConfig = {
  /** Maximum number of concurrent LLM API calls across all workflow instances.
   *  Overridden by MAX_CONCURRENT_LLM_CALLS env var. Default: 3 */
  maxConcurrentCalls: number;
};

// ── NL Interaction ──

/** Configuration for natural-language interaction with waiting workflows. */
export type NlInteractionConfig = {
  /** Master switch for NL interaction. When false, L1 detection and hint
   *  injection are completely disabled — all user messages go through Agent.
   *  Default: true. Overridden by CLAWMIND_NL_INTERACTION env var. */
  enabled: boolean;
  /** Enable isExactMatch intent detection. When a user message exactly matches
   *  a confirm/reject/choice keyword, the workflow is triggered without going
   *  through the Agent. Default: true */
  exactMatch: boolean;
  /** Enable hint injection. When a flow is waiting but the user message doesn't
   *  exactly match a keyword, inject a hint into the Agent context so it can
   *  recognize the workflow waiting state. Default: true */
  hintEnabled: boolean;
};

// ── Execution Engine ──

/** Configuration for workflow execution engine behavior. */
export type ExecutionConfig = {
  /** Timeout for long-running workflow commands (run, test) in milliseconds.
   *  When the command exceeds this timeout, it returns a friendly message
   *  while the workflow continues in the background. Default: 600000 (10 min).
   *  Set to 0 to disable timeout. Overridden by WORKFLOW_RUN_TIMEOUT_MS env var. */
  runTimeoutMs: number;
  /** Enable asynchronous workflow execution. When true, the `run` command
   *  returns immediately and the workflow executes in the background,
   *  with progress notifications pushed via chatInject. Default: false.
   *  Overridden by WORKFLOW_ASYNC_RUN env var. */
  asyncRun: boolean;
  /** Flow-timeout WATCHDOG: reap flows stuck in "running" longer than this many
   *  minutes and mark them failed — the only safety net against zombie/abandoned
   *  flows sitting in "running" forever. Set to 0 to DISABLE the watchdog entirely
   *  (let nodes run to completion with no reap). Default: 30.
   *  Overridden by CLAWMIND_FLOW_TIMEOUT_MINUTES env var. */
  flowTimeoutMinutes: number;
  /** How often (seconds) the flow-timeout watchdog sweeps for stale flows.
   *  Default: 120. Overridden by CLAWMIND_FLOW_REAP_INTERVAL_SECS env var. */
  flowReapIntervalSecs: number;
};

// ── TeClaw MCP Integration ──

/** Configuration for TeClaw MCP dual-channel integration (RFC-003). */
export type TeClawConfig = {
  /** Master switch for TeClaw integration. When false, TeClaw WS/HTTP is disabled. Default: false */
  enabled: boolean;
  /** Channel 2 — WebSocket URL (e.g., "wss://angw.andc-inc.cn/ws/v1/chat").
   *  Preferred over chatInjectUrl. Overridden by TECLAW_WS_URL env var. */
  wsUrl: string;
  /** MCP Token for WebSocket authentication. Overridden by TECLAW_WS_TOKEN env var. */
  wsToken: string;
  /** Additional HTTP headers for WebSocket handshake (e.g., {"x-andc-target-service":"tautie"}).
   *  Overridden by TECLAW_WS_HEADERS env var. */
  wsHeaders: Record<string, string>;
  /** Channel 2 — HTTP fallback chat/inject URL (DEPRECATED, prefer wsUrl).
   *  Overridden by TECLAW_CHAT_INJECT_URL env var. */
  chatInjectUrl: string;
  /** API key for chat/inject HTTP authentication (DEPRECATED, prefer wsToken).
   *  Overridden by TECLAW_CHAT_INJECT_KEY env var. */
  chatInjectKey: string;
  /** TeClaw HTTP base URL for REST API calls (e.g., session creation).
   *  Derived from wsUrl if not set (ws:// → http://, wss:// → https://, strip /ws/v1/chat).
   *  Overridden by TECLAW_HTTP_BASE_URL env var. */
  httpBaseUrl: string;
  /** TeClaw server base URL (DEPRECATED, prefer wsUrl).
   *  Overridden by TECLAW_BASE_URL env var. */
  baseUrl: string;
  /** Agent Loop URL (DEPRECATED, prefer wsUrl).
   *  Overridden by TECLAW_AGENT_LOOP_URL env var. */
  agentLoopUrl: string;
};

// ── ChatInject Notification ──

/** Configuration for chatInject notification level. */
export type ChatInjectConfig = {
  /** Unified inject level: "perf" | "simple" | "full". Default: "full".
   *  Replaces the legacy verbosity + performanceMode knobs (see below).
   *  Overridden by CLAWMIND_CHAT_INJECT_LEVEL env var. */
  level: import("../inject-level.js").InjectLevel;
  /** @deprecated Use {@link level}. Legacy verbosity — mapped to level at config load. */
  verbosity: "minimal" | "default" | "debug";
  /** @deprecated Use {@link level}. Legacy performance mode — mapped to level at config load. */
  performanceMode: boolean;
};

// ── Git Versioning ──

export type GitConfig = {
  /** Remote git repository URL for pack versioning. */
  remoteUrl: string;
  /** Git username for credential injection. */
  username: string;
  /** Git token/password. Override via CLAWMIND_GIT_TOKEN env var. */
  token: string;
  /** Git commit author email. Corporate git servers require valid company email (e.g. @antgroup.com). Override via CLAWMIND_GIT_EMAIL env var. */
  email: string;
};

// ── Flow Control ──

// ── Guardian Agent ──

export type GuardianAppConfig = {
  enabled: boolean;
  analysisTimeoutSeconds: number;
  maxPromptMultiplier: number;
};

// ── Aggregate Config ──

/** Full application configuration returned by loadConfig(). */
export type AppConfig = {
  /** Application name (e.g. "clawmind"). */
  appName: string;
  /** Semantic version of the ClawMind engine. */
  version: string;
  /**
   * Engine identifier — which host platform is running ClawMind.
   * When set, overrides auto-detection in resolveEngineName().
   * Valid values: "openclaw" | "claudecode" | "teclaw" | "hermes" | "cli".
   * Overridden by CLAWMIND_ENGINE env var.
   * Default: undefined (auto-detect from PlatformType + environment).
   */
  engine?: string;
  statePersistence: StatePersistenceConfig;
  knowledge: KnowledgeConfig;
  retry: RetryConfig;
  analysis: AnalysisConfig;
  alerting: AlertingConfig;
  api: ApiConfig;
  scheduler: SchedulerConfig;
  webhook: WebhookConfig;
  asyncCallback: AsyncCallbackConfig;
  contextCompression: ContextCompressionDefaults;
  flowControl: FlowControlConfig;
  sessionCompression: SessionCompressionAppConfig;
  llm: LlmConfig;
  execution: ExecutionConfig;
  nlInteraction: NlInteractionConfig;
  teclaw: TeClawConfig;
  chatInject: ChatInjectConfig;
  git: GitConfig;
  guardian: GuardianAppConfig;
};

/** Default configuration values. */
export const defaults: AppConfig = {
  appName: "clawmind",
  version: "0.1.0",
  statePersistence: {
    enabled: true,
    // Per-field byte cap for input/output JSON persisted to node_executions.
    // 32KB: ~3× the old 10KB, safely under the output_json TEXT column (≈64KB)
    // and leaves headroom for input+output sharing one API request body.
    // See NodeExecutionApiRepository / truncateJson for the enforcement path.
    maxIoSizeKb: 32,
    recordMetrics: true,
  },
  knowledge: {
    enabled: false,
    timeoutMs: 3000,
    cacheTtlMs: 60000,
    maxResults: 5,
    sources: {
      yuque: { enabled: false, token: "", domain: "yuque.antfin.com", namespaces: [] },
      agentmind: { enabled: false, token: "", endpoint: "", knowledgeBaseId: "" },
    },
  },
  retry: {
    kbSearchEnabled: false,
    maxAutoRetry: 1,
    errorContextMaxEntries: 20,
  },
  analysis: {
    enabled: false,
    thresholds: { healthScore: 0.6, toolFailureRate: 0.3, incompleteRate: 0.3 },
  },
  alerting: {
    enabled: false,
    onNodeFailure: true,
    dingtalk: { webhooks: [], keywords: [] },
  },
  api: {
    enabled: false,
    port: 3210,
    host: "127.0.0.1",
    apiKey: "",
    baseUrl: "http://localhost:3001",
    privateKeyB64: "",
    iamtoken: "",
    timeout: 5000,
    maxRetries: 3,
    clawwebUrl: "https://clawweb-pre.antgroup-inc.cn",
    corpId: "",
  },
  scheduler: {
    enabled: false,
    pollIntervalMs: 60_000,
    defaultMaxConcurrent: 1,
    missedFirePolicy: "fireLast",
  },
  webhook: {
    enabled: false,
    maxPayloadKb: 1024,
    idempotencyWindowHours: 24,
    eventRetentionDays: 30,
  },
  asyncCallback: {
    enabled: true,
    callbackBaseUrl: "",
    defaultTimeout: "1h",
    timeoutPollIntervalMs: 60_000,
    defaultHmacSecret: "",
    maxCallbackPayloadKb: 256,
    tokenRetentionDays: 7,
  },
  contextCompression: {
    enabled: false,
    defaultMaxTokens: 8000,
    warningThresholdRatio: 0.7,
    defaultOverflowStrategy: "priority-evict",
    defaultSteps: [
      { strategy: "dedup" },
      { strategy: "error-purge", params: { maxAgeTurns: 2 } },
    ],
  },
  flowControl: {
    enabled: false,
    perWorkflow: {},
    dispatcher: { pollIntervalMs: 1000 },
  },
  sessionCompression: {
    toolPrepassEnabled: true,
    toolResultMaxChars: 5000,
    recencyWindow: 6,
    maxSessionTokens: 50000,
    insertCompactionNotice: true,
    deduplicateReads: true,
    readDedupTtlMs: 300_000,
    minTokensToCompact: 30000,
    mainSessionEnabled: false,
  },
  llm: {
    maxConcurrentCalls: 3,
  },
  execution: {
    runTimeoutMs: 600_000,
    asyncRun: false,
    flowTimeoutMinutes: 30,
    flowReapIntervalSecs: 120,
  },
  nlInteraction: {
    enabled: true,
    exactMatch: true,
    hintEnabled: true,
  },
  teclaw: {
    enabled: false,
    wsUrl: "",
    wsToken: "",
    wsHeaders: {},
    chatInjectUrl: "",
    chatInjectKey: "",
    httpBaseUrl: "",
    baseUrl: "",
    agentLoopUrl: "",
  },
  chatInject: {
    level: "full",
    verbosity: "default",
    performanceMode: false,
  },
  git: {
    remoteUrl: "",
    username: "",
    token: "",
    email: "",
  },
  guardian: {
    enabled: true,
    analysisTimeoutSeconds: 60,
    maxPromptMultiplier: 2,
  },
};