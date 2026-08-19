/**
 * Application configuration loader.
 *
 * Extends the database config loader with all new capability sections:
 * statePersistence, knowledge, retry, analysis, alerting, api.
 *
 * Each section has sensible defaults so the engine works without
 * any configuration — new capabilities are opt-in.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { parse as parseYaml } from "yaml";
import type { DatabaseConfig, MySqlConfig, IDatabase } from "../db/types.js";
import type { ApiClient } from "../db/api-client.js";
import { getCachedConfig, setCachedConfig, isConfigReady } from "./config-cache.js";
import { loadAppConfigFromDB } from "./db-config-loader.js";
import type {
  AppConfig,
  StatePersistenceConfig,
  KnowledgeConfig,
  YuQueSourceConfig,
  AgentMindSourceConfig,
  RetryConfig,
  AnalysisConfig,
  ThresholdsConfig,
  AlertingConfig,
  DingTalkConfig,
  ApiConfig,
  SchedulerConfig,
  WebhookConfig,
  ContextCompressionDefaults,
  SessionCompressionAppConfig,
  LlmConfig,
  ExecutionConfig,
  NlInteractionConfig,
  TeClawConfig,
  ChatInjectConfig,
  AsyncCallbackConfig,
} from "./types.js";
import type { CompressionStrategy, CompressionStep } from "../context/types.js";
import { loadFlowControlConfig } from "../flow-control/config.js";
import { defaults } from "./types.js";

// ── YAML shape types ──

type YamlDatabaseConfig = {
  mode?: "sqlite" | "prod";
  sqlite?: { path?: string };
  zdas?: {
    enabled?: boolean;
    datasources?: Array<{
      database?: string;
      user?: string;
      password?: string;
      host?: string;
      port?: string | number;
      pool_size?: number;
      pool_min?: number;
    }>;
  };
  /** API mode settings (fallback: top-level api: is also used via parseApi merge). */
  api?: YamlApiConfig;
};

type YamlStatePersistenceConfig = {
  enabled?: boolean;
  maxIoSizeKb?: number;
  recordMetrics?: boolean;
};

type YamlYuQueSourceConfig = {
  enabled?: boolean;
  token?: string;
  domain?: string;
  namespaces?: string[];
};

type YamlAgentMindSourceConfig = {
  enabled?: boolean;
  token?: string;
  endpoint?: string;
  knowledgeBaseId?: string;
};

type YamlKnowledgeConfig = {
  enabled?: boolean;
  timeoutMs?: number;
  cacheTtlMs?: number;
  maxResults?: number;
  sources?: {
    yuque?: YamlYuQueSourceConfig;
    agentmind?: YamlAgentMindSourceConfig;
  };
};

type YamlRetryConfig = {
  kbSearchEnabled?: boolean;
  maxAutoRetry?: number;
  errorContextMaxEntries?: number;
};

type YamlThresholdsConfig = {
  healthScore?: number;
  toolFailureRate?: number;
  incompleteRate?: number;
};

type YamlAnalysisConfig = {
  enabled?: boolean;
  thresholds?: YamlThresholdsConfig;
};

type YamlDingTalkConfig = {
  webhooks?: string[];
  keywords?: string[];
};

type YamlAlertingConfig = {
  enabled?: boolean;
  onNodeFailure?: boolean;
  dingtalk?: YamlDingTalkConfig;
};

type YamlApiConfig = {
  enabled?: boolean;
  port?: number;
  host?: string;
  apiKey?: string;
  baseUrl?: string;
  privateKeyB64?: string;
  iamtoken?: string;
  timeout?: number;
  maxRetries?: number;
  clawwebUrl?: string;
  corpId?: string;
};

type YamlSchedulerConfig = {
  enabled?: boolean;
  pollIntervalMs?: number;
  defaultMaxConcurrent?: number;
  missedFirePolicy?: "skip" | "fireLast" | "fireAll";
};

type YamlWebhookConfig = {
  enabled?: boolean;
  maxPayloadKb?: number;
  idempotencyWindowHours?: number;
  eventRetentionDays?: number;
};

type YamlCompressionStep = {
  strategy?: string;
  params?: Record<string, unknown>;
};

type YamlContextCompressionConfig = {
  enabled?: boolean;
  defaultMaxTokens?: number;
  warningThresholdRatio?: number;
  defaultOverflowStrategy?: string;
  defaultSteps?: YamlCompressionStep[];
};

type YamlSessionCompressionConfig = {
  toolPrepassEnabled?: boolean;
  toolResultMaxChars?: number;
  recencyWindow?: number;
  maxSessionTokens?: number;
  insertCompactionNotice?: boolean;
  deduplicateReads?: boolean;
  readDedupTtlMs?: number;
  minTokensToCompact?: number;
  contextTokenBudget?: number;
  modelContextWindow?: number;
  mainSessionEnabled?: boolean;
};

type YamlLlmConfig = {
  /** Maximum number of concurrent LLM API calls. Overridden by MAX_CONCURRENT_LLM_CALLS env var. */
  maxConcurrentCalls?: number;
};

type YamlExecutionConfig = {
  /** Timeout for long-running workflow commands in ms. Overridden by WORKFLOW_RUN_TIMEOUT_MS env var. */
  runTimeoutMs?: number;
  /** Enable asynchronous workflow execution. Overridden by WORKFLOW_ASYNC_RUN env var. */
  asyncRun?: boolean;
  /** Flow-timeout watchdog minutes. Overridden by CLAWMIND_FLOW_TIMEOUT_MINUTES env var. */
  flowTimeoutMinutes?: number;
  /** Watchdog sweep interval in seconds. Overridden by CLAWMIND_FLOW_REAP_INTERVAL_SECS env var. */
  flowReapIntervalSecs?: number;
};

type YamlNlInteractionConfig = {
  enabled?: boolean;
  exactMatch?: boolean;
  hintEnabled?: boolean;
};

type YamlTeClawConfig = {
  enabled?: boolean;
  wsUrl?: string;
  wsToken?: string;
  wsHeaders?: Record<string, string>;
  chatInjectUrl?: string;
  chatInjectKey?: string;
  httpBaseUrl?: string;
  baseUrl?: string;
  agentLoopUrl?: string;
};

type YamlChatInjectConfig = {
  /** Unified inject level. Overrides legacy verbosity/performanceMode when set. */
  level?: "perf" | "simple" | "full";
  /** @deprecated Legacy verbosity. */
  verbosity?: "minimal" | "default" | "debug";
  /** @deprecated Legacy performance mode. */
  performanceMode?: boolean;
};

type YamlGitConfig = {
  remoteUrl?: string;
  username?: string;
  token?: string;
  /** Git commit author email. Corporate git servers (e.g. code.alipay.com) require valid company email. */
  email?: string;
};

export type YamlPacksConfig = {
  /** Additional pack search roots (multi-root union; realpath-deduped). */
  roots?: string[];
  /** Per-engine pack roots; only the entry matching the current engine is loaded. */
  perEngine?: Record<string, string | string[]>;
};

type YamlAppConfig = {
  app_name?: string;
  version?: string;
  /** Engine identifier override (e.g. "openclaw", "claudecode", "teclaw", "hermes", "cli"). */
  engine?: string;
  database?: YamlDatabaseConfig;
  statePersistence?: YamlStatePersistenceConfig;
  knowledge?: YamlKnowledgeConfig;
  retry?: YamlRetryConfig;
  analysis?: YamlAnalysisConfig;
  alerting?: YamlAlertingConfig;
  api?: YamlApiConfig;
  scheduler?: YamlSchedulerConfig;
  webhook?: YamlWebhookConfig;
  contextCompression?: YamlContextCompressionConfig;
  sessionCompression?: YamlSessionCompressionConfig;
  llm?: YamlLlmConfig;
  execution?: YamlExecutionConfig;
  nlInteraction?: YamlNlInteractionConfig;
  teclaw?: YamlTeClawConfig;
  chatInject?: YamlChatInjectConfig;
  flowControl?: Record<string, unknown>;
  asyncCallback?: Record<string, unknown>;
  git?: YamlGitConfig;
  packs?: YamlPacksConfig;
  guardian?: {
    enabled?: boolean;
    analysisTimeoutSeconds?: number;
    maxPromptMultiplier?: number;
  };
};

type OpenClawPluginConfig = Omit<Partial<YamlAppConfig>, "database">;

// ── Config search paths ──

const CONFIG_FILENAME = "application.yaml";

/** Plugin ID as defined in openclaw.plugin.json */
const PLUGIN_ID = "clawmind";

/**
 * Known OpenClaw extension directories (local dev + production).
 * The loader probes each in order until it finds a config file.
 */
const KNOWN_EXTENSION_DIRS = [
  () => join(homedir(), ".openclaw", "extensions", PLUGIN_ID),
  () => join(homedir(), "openclawExt", "clawmind"),
  () => "/home/admin/openclawExt/clawmind",
  () => "/usr/local/openclaw/extensions/clawmind",
];

/**
 * Find the plugin's own config file.
 *
 * Search order:
 * 1. Explicit path (if provided)
 * 2. CLAWMIND_CONFIG_PATH env var (exact file path)
 * 3. OPENCLAW_EXTENSIONS_DIR env var → {dir}/clawmind/configs/application.yaml
 * 4. Known plugin installation directories
 * 5. Walk up from this file's location to find package.json + configs/
 *
 * NOTE: import.meta.dirname / import.meta.url may point to a jiti cache
 * directory when running inside OpenClaw, so we prefer known paths first.
 */
function findConfigFile(explicitPath?: string): string | null {
  if (explicitPath && existsSync(explicitPath)) {
    console.error(`[config] findConfigFile: explicitPath=${explicitPath} (exists)`);
    return explicitPath;
  }

  // 1. Explicit config file path via env var
  const envPath = getEnv("CLAWMIND_CONFIG_PATH");
  if (envPath && existsSync(envPath)) {
    console.error(`[config] findConfigFile: CLAWMIND_CONFIG_PATH=${envPath} (exists)`);
    return envPath;
  }

  // 2. OpenClaw extensions dir via env var
  const extDir = getEnv("OPENCLAW_EXTENSIONS_DIR");
  if (extDir) {
    const candidate = join(extDir, PLUGIN_ID, "configs", CONFIG_FILENAME);
    if (existsSync(candidate)) {
      console.error(`[config] findConfigFile: OPENCLAW_EXTENSIONS_DIR candidate=${candidate} (exists)`);
      return candidate;
    }
    console.error(`[config] findConfigFile: OPENCLAW_EXTENSIONS_DIR candidate=${candidate} (not found)`);
  }

  // 3. Known installation directories
  for (const dirFn of KNOWN_EXTENSION_DIRS) {
    const dir = dirFn();
    const candidate = join(dir, "configs", CONFIG_FILENAME);
    if (existsSync(candidate)) {
      console.error(`[config] findConfigFile: knownDir candidate=${candidate} (exists)`);
      return candidate;
    }
    console.error(`[config] findConfigFile: knownDir candidate=${candidate} (not found)`);
  }

  // 4. Fallback: walk up from this file's location to find package.json + configs/
  //    NOTE: dist/ and dist/esm/ may contain a stub package.json (e.g. {"type":"module"}),
  //    so we do NOT stop at the first package.json — we continue walking up until we find
  //    a directory that has BOTH package.json AND configs/application.yaml.
  let thisDir: string;
  try {
    thisDir = import.meta.dirname;
  } catch {
    thisDir = new URL(".", import.meta.url).pathname;
  }
  console.error(`[config] findConfigFile: walk-up starting from thisDir=${thisDir}`);
  for (let dir = thisDir, i = 0; i < 20; i++) {
    const pjPath = join(dir, "package.json");
    const hasPackageJson = existsSync(pjPath);
    const candidate = hasPackageJson ? join(dir, "configs", CONFIG_FILENAME) : null;
    const hasConfig = candidate ? existsSync(candidate) : false;
    if (hasPackageJson) {
      console.error(`[config] findConfigFile: walk-up dir=${dir}, package.json=exists, configs/application.yaml=${hasConfig ? "exists" : "MISSING"}, candidate=${candidate}`);
      if (hasConfig) return candidate;
      // Do NOT break — keep walking up; dist/esm/package.json is a stub without configs/.
    }
    const parent = join(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }

  console.error(`[config] findConfigFile: NO config file found, using defaults`);
  return null;
}

// ── Env var helpers ──

function getEnv(key: string): string | undefined {
  return process.env[key];
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function deepMerge<T extends Record<string, unknown>>(
  base: T | undefined,
  override: Partial<T> | undefined,
): T | undefined {
  if (!base) return override as T | undefined;
  if (!override) return base;
  const merged: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(override)) {
    const existing = merged[key];
    merged[key] = isPlainObject(existing) && isPlainObject(value)
      ? deepMerge(existing, value)
      : value;
  }
  return merged as T;
}

function readOpenClawPluginConfig(options: { useDefaultPath: boolean }): OpenClawPluginConfig {
  const explicitPath = getEnv("OPENCLAW_CONFIG_PATH");
  const configPath = explicitPath ?? (options.useDefaultPath ? join(homedir(), ".openclaw", "openclaw.json") : "");
  if (!configPath) return {};
  if (!existsSync(configPath)) return {};

  try {
    const raw = JSON.parse(readFileSync(configPath, "utf-8")) as Record<string, unknown>;
    const plugins = isPlainObject(raw.plugins) ? raw.plugins : undefined;
    const entries = plugins && isPlainObject(plugins.entries) ? plugins.entries : undefined;
    const clawmind = entries && isPlainObject(entries[PLUGIN_ID])
      ? entries[PLUGIN_ID]
      : entries && isPlainObject(entries.ClawMind)
        ? entries.ClawMind
        : undefined;
    if (!clawmind) return {};

    if (isPlainObject(clawmind.config)) {
      return clawmind.config as OpenClawPluginConfig;
    }

    return {};
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[config] Failed to read OpenClaw plugin config ${configPath}: ${msg}, ignoring`);
    return {};
  }
}

function envInt(key: string, fallback: number): number {
  const raw = getEnv(key);
  if (!raw) return fallback;
  const parsed = parseInt(raw, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

function envFloat(key: string, fallback: number): number {
  const raw = getEnv(key);
  if (!raw) return fallback;
  const parsed = parseFloat(raw);
  return Number.isNaN(parsed) ? fallback : parsed;
}

function envString(key: string, fallback: string): string {
  return getEnv(key) ?? fallback;
}

// ── Section parsers ──

function parseStatePersistence(raw?: YamlStatePersistenceConfig): StatePersistenceConfig {
  if (!raw) return { ...defaults.statePersistence };
  return {
    enabled: raw.enabled ?? defaults.statePersistence.enabled,
    maxIoSizeKb: raw.maxIoSizeKb ?? defaults.statePersistence.maxIoSizeKb,
    recordMetrics: raw.recordMetrics ?? defaults.statePersistence.recordMetrics,
  };
}

function parseYuQueSource(raw?: YamlYuQueSourceConfig): YuQueSourceConfig {
  const d = defaults.knowledge.sources.yuque;
  if (!raw) return { ...d };
  return {
    enabled: raw.enabled ?? d.enabled,
    token: envString("YUQUE_TOKEN", raw.token ?? d.token),
    domain: raw.domain ?? d.domain,
    namespaces: raw.namespaces ?? d.namespaces,
  };
}

function parseAgentMindSource(raw?: YamlAgentMindSourceConfig): AgentMindSourceConfig {
  const d = defaults.knowledge.sources.agentmind;
  if (!raw) return { ...d };
  return {
    enabled: raw.enabled ?? d.enabled,
    token: envString("AGENTMIND_TOKEN", raw.token ?? d.token),
    endpoint: raw.endpoint ?? d.endpoint,
    knowledgeBaseId: raw.knowledgeBaseId ?? d.knowledgeBaseId,
  };
}

function parseKnowledge(raw?: YamlKnowledgeConfig): KnowledgeConfig {
  if (!raw) return { ...defaults.knowledge, sources: { yuque: { ...defaults.knowledge.sources.yuque }, agentmind: { ...defaults.knowledge.sources.agentmind } } };
  return {
    enabled: raw.enabled ?? defaults.knowledge.enabled,
    timeoutMs: raw.timeoutMs ?? defaults.knowledge.timeoutMs,
    cacheTtlMs: raw.cacheTtlMs ?? defaults.knowledge.cacheTtlMs,
    maxResults: raw.maxResults ?? defaults.knowledge.maxResults,
    sources: {
      yuque: parseYuQueSource(raw.sources?.yuque),
      agentmind: parseAgentMindSource(raw.sources?.agentmind),
    },
  };
}

function parseRetry(raw?: YamlRetryConfig): RetryConfig {
  if (!raw) return { ...defaults.retry };
  return {
    kbSearchEnabled: raw.kbSearchEnabled ?? defaults.retry.kbSearchEnabled,
    maxAutoRetry: raw.maxAutoRetry ?? defaults.retry.maxAutoRetry,
    errorContextMaxEntries: raw.errorContextMaxEntries ?? defaults.retry.errorContextMaxEntries,
  };
}

function parseAnalysis(raw?: YamlAnalysisConfig): AnalysisConfig {
  if (!raw) return { ...defaults.analysis, thresholds: { ...defaults.analysis.thresholds } };
  const dt = defaults.analysis.thresholds;
  const thresholds: ThresholdsConfig = {
    healthScore: raw.thresholds?.healthScore ?? dt.healthScore,
    toolFailureRate: raw.thresholds?.toolFailureRate ?? dt.toolFailureRate,
    incompleteRate: raw.thresholds?.incompleteRate ?? dt.incompleteRate,
  };
  return {
    enabled: raw.enabled ?? defaults.analysis.enabled,
    thresholds,
  };
}

function parseAlerting(raw?: YamlAlertingConfig): AlertingConfig {
  if (!raw) return { ...defaults.alerting, dingtalk: { ...defaults.alerting.dingtalk } };
  const dd = defaults.alerting.dingtalk;

  const dingtalk: DingTalkConfig = {
    webhooks: raw.dingtalk?.webhooks ?? dd.webhooks,
    keywords: raw.dingtalk?.keywords ?? dd.keywords,
  };
  return {
    enabled: raw.enabled ?? defaults.alerting.enabled,
    onNodeFailure: raw.onNodeFailure ?? defaults.alerting.onNodeFailure,
    dingtalk,
  };
}

/**
 * Parse the top-level `api:` config section.
 * Falls back to `database.api:` values for backward compatibility when
 * the top-level key is absent — this allows a single `api:` block
 * at the top level to serve both the app layer and the database layer.
 */
function parseApi(raw?: YamlApiConfig, dbApi?: YamlApiConfig): ApiConfig {
  // Top-level `api:` takes priority; `database.api:` fills gaps only.
  const source: YamlApiConfig = raw ?? dbApi ?? {};

  return {
    enabled: source.enabled ?? defaults.api.enabled,
    port: envInt("WORKFLOW_API_PORT", source.port ?? defaults.api.port),
    host: envString("WORKFLOW_API_HOST", source.host ?? defaults.api.host),
    apiKey: envString("WORKFLOW_API_KEY", source.apiKey ?? defaults.api.apiKey),
    baseUrl: envString("CLAWWEB_API_URL", source.baseUrl ?? defaults.api.baseUrl),
    privateKeyB64: envString("CLAWMIND_PRIVATE_KEY", source.privateKeyB64 ?? defaults.api.privateKeyB64),
    iamtoken: envString("CLAWMIND_IAMTOKEN", source.iamtoken ?? defaults.api.iamtoken),
    timeout: source.timeout ?? defaults.api.timeout,
    maxRetries: source.maxRetries ?? defaults.api.maxRetries,
    clawwebUrl: envString("CLAWWEB_URL", source.clawwebUrl ?? source.baseUrl ?? defaults.api.clawwebUrl),
    corpId: envString("DINGTALK_CORP_ID", source.corpId ?? defaults.api.corpId),
  };
}

function envBool(key: string, fallback: boolean): boolean {
  const raw = getEnv(key);
  if (!raw) return fallback;
  return raw.toLowerCase() === "true" || raw === "1";
}

function parseScheduler(raw?: YamlSchedulerConfig): SchedulerConfig {
  if (!raw) return { ...defaults.scheduler };
  const validPolicies: ReadonlyArray<"skip" | "fireLast" | "fireAll"> = ["skip", "fireLast", "fireAll"];
  const envPolicy = getEnv("SCHEDULER_MISSED_FIRE_POLICY");
  const policyRaw = envPolicy ?? raw.missedFirePolicy ?? defaults.scheduler.missedFirePolicy;
  const missedFirePolicy = validPolicies.includes(policyRaw as "skip" | "fireLast" | "fireAll")
    ? (policyRaw as "skip" | "fireLast" | "fireAll")
    : defaults.scheduler.missedFirePolicy;
  return {
    enabled: envBool("SCHEDULER_ENABLED", raw.enabled ?? defaults.scheduler.enabled),
    pollIntervalMs: envInt("SCHEDULER_POLL_INTERVAL_MS", raw.pollIntervalMs ?? defaults.scheduler.pollIntervalMs),
    defaultMaxConcurrent: envInt("SCHEDULER_DEFAULT_MAX_CONCURRENT", raw.defaultMaxConcurrent ?? defaults.scheduler.defaultMaxConcurrent),
    missedFirePolicy,
  };
}

function parseWebhook(raw?: YamlWebhookConfig): WebhookConfig {
  if (!raw) return { ...defaults.webhook };
  return {
    enabled: envBool("WEBHOOK_ENABLED", raw.enabled ?? defaults.webhook.enabled),
    maxPayloadKb: envInt("WEBHOOK_MAX_PAYLOAD_KB", raw.maxPayloadKb ?? defaults.webhook.maxPayloadKb),
    idempotencyWindowHours: envInt("WEBHOOK_IDEMPOTENCY_WINDOW_HOURS", raw.idempotencyWindowHours ?? defaults.webhook.idempotencyWindowHours),
    eventRetentionDays: envInt("WEBHOOK_EVENT_RETENTION_DAYS", raw.eventRetentionDays ?? defaults.webhook.eventRetentionDays),
  };
}

function parseAsyncCallback(raw?: Record<string, unknown>): AsyncCallbackConfig {
  if (!raw) return { ...defaults.asyncCallback };
  return {
    enabled: envBool("ASYNC_CALLBACK_ENABLED", (raw.enabled as boolean) ?? defaults.asyncCallback.enabled),
    callbackBaseUrl: envString("ASYNC_CALLBACK_BASE_URL", (raw.callbackBaseUrl as string) ?? defaults.asyncCallback.callbackBaseUrl),
    defaultTimeout: envString("ASYNC_CALLBACK_DEFAULT_TIMEOUT", (raw.defaultTimeout as string) ?? defaults.asyncCallback.defaultTimeout),
    timeoutPollIntervalMs: envInt("ASYNC_CALLBACK_TIMEOUT_POLL_INTERVAL_MS", (raw.timeoutPollIntervalMs as number) ?? defaults.asyncCallback.timeoutPollIntervalMs),
    defaultHmacSecret: envString("ASYNC_CALLBACK_HMAC_SECRET", (raw.defaultHmacSecret as string) ?? defaults.asyncCallback.defaultHmacSecret),
    maxCallbackPayloadKb: envInt("ASYNC_CALLBACK_MAX_PAYLOAD_KB", (raw.maxCallbackPayloadKb as number) ?? defaults.asyncCallback.maxCallbackPayloadKb),
    tokenRetentionDays: envInt("ASYNC_CALLBACK_TOKEN_RETENTION_DAYS", (raw.tokenRetentionDays as number) ?? defaults.asyncCallback.tokenRetentionDays),
  };
}

function parseLlm(raw?: YamlLlmConfig): LlmConfig {
  if (!raw) return { ...defaults.llm };
  return {
    maxConcurrentCalls: envInt("MAX_CONCURRENT_LLM_CALLS", raw.maxConcurrentCalls ?? defaults.llm.maxConcurrentCalls),
  };
}

function parseExecution(raw?: YamlExecutionConfig): ExecutionConfig {
  if (!raw) return { ...defaults.execution };
  return {
    runTimeoutMs: envInt("WORKFLOW_RUN_TIMEOUT_MS", raw.runTimeoutMs ?? defaults.execution.runTimeoutMs),
    asyncRun: envBool("WORKFLOW_ASYNC_RUN", raw.asyncRun ?? defaults.execution.asyncRun),
    flowTimeoutMinutes: envInt("CLAWMIND_FLOW_TIMEOUT_MINUTES", raw.flowTimeoutMinutes ?? defaults.execution.flowTimeoutMinutes),
    flowReapIntervalSecs: envInt("CLAWMIND_FLOW_REAP_INTERVAL_SECS", raw.flowReapIntervalSecs ?? defaults.execution.flowReapIntervalSecs),
  };
}

export function parseNlInteraction(raw?: YamlNlInteractionConfig): NlInteractionConfig {
  if (!raw) return { ...defaults.nlInteraction };
  return {
    enabled: envBool("CLAWMIND_NL_INTERACTION", raw.enabled ?? defaults.nlInteraction.enabled),
    exactMatch: raw.exactMatch ?? defaults.nlInteraction.exactMatch,
    hintEnabled: raw.hintEnabled ?? defaults.nlInteraction.hintEnabled,
  };
}

function parseTeClaw(raw?: YamlTeClawConfig): TeClawConfig {
  if (!raw) return { ...defaults.teclaw, wsHeaders: { ...defaults.teclaw.wsHeaders } };
  // wsHeaders can come from YAML object or TECLAW_WS_HEADERS env var (JSON string)
  let wsHeaders = raw.wsHeaders ?? { ...defaults.teclaw.wsHeaders };
  const wsHeadersEnv = getEnv("TECLAW_WS_HEADERS");
  if (wsHeadersEnv) {
    try {
      wsHeaders = JSON.parse(wsHeadersEnv) as Record<string, string>;
    } catch {
      console.warn("[config] TECLAW_WS_HEADERS is not valid JSON, ignoring");
    }
  }
  return {
    enabled: envBool("TECLAW_ENABLED", raw.enabled ?? defaults.teclaw.enabled),
    wsUrl: envString("TECLAW_WS_URL", raw.wsUrl ?? defaults.teclaw.wsUrl),
    wsToken: envString("TECLAW_WS_TOKEN", raw.wsToken ?? defaults.teclaw.wsToken),
    wsHeaders,
    chatInjectUrl: envString("TECLAW_CHAT_INJECT_URL", raw.chatInjectUrl ?? defaults.teclaw.chatInjectUrl),
    chatInjectKey: envString("TECLAW_CHAT_INJECT_KEY", raw.chatInjectKey ?? defaults.teclaw.chatInjectKey),
    httpBaseUrl: envString("TECLAW_HTTP_BASE_URL", raw.httpBaseUrl ?? defaults.teclaw.httpBaseUrl),
    baseUrl: envString("TECLAW_BASE_URL", raw.baseUrl ?? defaults.teclaw.baseUrl),
    agentLoopUrl: envString("TECLAW_AGENT_LOOP_URL", raw.agentLoopUrl ?? defaults.teclaw.agentLoopUrl),
  };
}

const VALID_INJECT_LEVELS: ReadonlyArray<import("../inject-level.js").InjectLevel> = ["perf", "simple", "full"];
const VALID_VERBOSITY_LEVELS: ReadonlyArray<"minimal" | "default" | "debug"> = ["minimal", "default", "debug"];

function legacyToLevel(verbosity: "minimal" | "default" | "debug", performanceMode: boolean): import("../inject-level.js").InjectLevel {
  if (performanceMode) return "perf";
  if (verbosity === "minimal") return "perf";
  if (verbosity === "debug") return "full";
  return "full"; // default
}

function parseChatInject(raw?: YamlChatInjectConfig): ChatInjectConfig {
  const defaultsLevel: import("../inject-level.js").InjectLevel = "full";
  if (!raw) {
    return { level: defaultsLevel, verbosity: "default", performanceMode: false };
  }
  const envLevel = getEnv("CLAWMIND_CHAT_INJECT_LEVEL");
  const envVerbosity = getEnv("CHATINJECT_VERBOSITY");
  const rawVerbosity = envVerbosity ?? raw.verbosity ?? "default";
  const verbosity = (VALID_VERBOSITY_LEVELS as readonly string[]).includes(rawVerbosity)
    ? (rawVerbosity as "minimal" | "default" | "debug")
    : "default";
  const performanceMode = envBool("CLAWMIND_PERFORMANCE_MODE", raw.performanceMode ?? false);

  // New `level` wins over legacy fields. Env `CLAWMIND_CHAT_INJECT_LEVEL` wins over yaml `level`.
  let level: import("../inject-level.js").InjectLevel | undefined;
  if (envLevel) {
    level = (VALID_INJECT_LEVELS as readonly string[]).includes(envLevel)
      ? (envLevel as import("../inject-level.js").InjectLevel)
      : undefined; // invalid → fallback below
    if (!level) {
      console.warn(`[config] invalid CLAWMIND_CHAT_INJECT_LEVEL=${envLevel}, falling back to full`);
    }
  }
  if (!level && raw.level) {
    level = (VALID_INJECT_LEVELS as readonly string[]).includes(raw.level)
      ? (raw.level as import("../inject-level.js").InjectLevel)
      : undefined;
    if (!level) {
      console.warn(`[config] invalid chatInject.level=${raw.level}, falling back to full`);
    }
  }
  if (!level) {
    level = legacyToLevel(verbosity, performanceMode);
  } else if (verbosity !== "default" || performanceMode || envVerbosity || process.env.CLAWMIND_PERFORMANCE_MODE) {
    // New level explicitly set AND legacy fields also set → level wins; warn.
    console.warn(
      `[config] chatInject.level=${level} overrides legacy verbosity=${verbosity}/performanceMode=${performanceMode}; migrate to chatInject.level (legacy fields are deprecated)`,
    );
  }
  return { level, verbosity, performanceMode };
}

const VALID_COMPRESSION_STRATEGIES: ReadonlyArray<CompressionStrategy> = [
  "verbatim", "dedup", "fuzzy-dedup", "error-purge", "truncate", "priority-evict", "key-value-extract", "sentence-score", "llm-summarize",
];

function parseCompressionStep(raw?: YamlCompressionStep): CompressionStep {
  const strategy = (VALID_COMPRESSION_STRATEGIES as readonly string[]).includes(raw?.strategy ?? "")
    ? (raw!.strategy as CompressionStrategy)
    : "dedup";
  return { strategy, ...(raw?.params ? { params: raw.params } : {}) };
}

function parseContextCompression(raw?: YamlContextCompressionConfig): ContextCompressionDefaults {
  if (!raw) return { ...defaults.contextCompression };
  const validStrategies = VALID_COMPRESSION_STRATEGIES as readonly string[];
  const overflowStrategy = validStrategies.includes(raw.defaultOverflowStrategy ?? "")
    ? (raw.defaultOverflowStrategy as CompressionStrategy)
    : defaults.contextCompression.defaultOverflowStrategy;
  const steps = raw.defaultSteps
    ? raw.defaultSteps.map((s) => parseCompressionStep(s))
    : defaults.contextCompression.defaultSteps;
  return {
    enabled: envBool("CONTEXT_COMPRESSION_ENABLED", raw.enabled ?? defaults.contextCompression.enabled),
    defaultMaxTokens: envInt("CONTEXT_COMPRESSION_MAX_TOKENS", raw.defaultMaxTokens ?? defaults.contextCompression.defaultMaxTokens),
    warningThresholdRatio: raw.warningThresholdRatio ?? defaults.contextCompression.warningThresholdRatio,
    defaultOverflowStrategy: overflowStrategy,
    defaultSteps: steps,
  };
}

function parseSessionCompression(raw?: YamlSessionCompressionConfig): SessionCompressionAppConfig {
  if (!raw) return { ...defaults.sessionCompression };
  return {
    toolPrepassEnabled: raw.toolPrepassEnabled ?? defaults.sessionCompression.toolPrepassEnabled,
    toolResultMaxChars: raw.toolResultMaxChars ?? defaults.sessionCompression.toolResultMaxChars,
    recencyWindow: raw.recencyWindow ?? defaults.sessionCompression.recencyWindow,
    maxSessionTokens: envInt("SESSION_COMPRESSION_MAX_SESSION_TOKENS", raw.maxSessionTokens ?? defaults.sessionCompression.maxSessionTokens),
    insertCompactionNotice: raw.insertCompactionNotice ?? defaults.sessionCompression.insertCompactionNotice,
    deduplicateReads: raw.deduplicateReads ?? defaults.sessionCompression.deduplicateReads,
    readDedupTtlMs: raw.readDedupTtlMs ?? defaults.sessionCompression.readDedupTtlMs,
    minTokensToCompact: envInt("SESSION_COMPRESSION_MIN_TOKENS", raw.minTokensToCompact ?? defaults.sessionCompression.minTokensToCompact),
    contextTokenBudget: raw.contextTokenBudget,
    modelContextWindow: raw.modelContextWindow,
    mainSessionEnabled: raw.mainSessionEnabled ?? defaults.sessionCompression.mainSessionEnabled,
  };
}

// ── Parse YAML file ──

function readYamlConfig(configPath?: string): YamlAppConfig {
  const filePath = findConfigFile(configPath);
  if (!filePath) return {};

  try {
    const raw = readFileSync(filePath, "utf-8");
    return parseYaml(raw) as YamlAppConfig;
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[config] Failed to read config file ${filePath}: ${msg}, using defaults`);
    return {};
  }
}

// ── Public API ──

/**
 * Load the full application configuration.
 *
 * Priority: env vars > DB cm_app_config > local application.yaml > built-in defaults.
 *
 * After initConfig() has completed, returns the cached merged config (sync, zero overhead).
 * Before initConfig() (engine startup early phase), returns local application.yaml config only.
 *
 * Merging is in-memory only — the local application.yaml file is never modified.
 */
export function loadConfig(configPath?: string): { database: DatabaseConfig; app: AppConfig } {
  // Return cached config if available (after initConfig has run)
  if (isConfigReady()) {
    return getCachedConfig()!;
  }

  // Cache not ready — build from local application.yaml only (startup early phase)
  if (!_localFallbackWarned) {
    _localFallbackWarned = true;
    console.warn("[config] loadConfig() called before initConfig() — using local application.yaml only (DB config not yet loaded)");
  }

  return buildConfigFromYaml(configPath);
}

let _localFallbackWarned = false;

/**
 * Build full config from local application.yaml + env vars (no DB).
 * This is the fallback path used before initConfig() completes.
 */
function buildConfigFromYaml(configPath?: string): { database: DatabaseConfig; app: AppConfig } {
  const yaml: YamlAppConfig = deepMerge<YamlAppConfig>(
    readYamlConfig(configPath),
    readOpenClawPluginConfig({ useDefaultPath: !configPath }),
  ) ?? {};

  return buildConfigFromMergedYaml(yaml);
}

/**
 * Build full config from a pre-merged YamlAppConfig + env vars.
 * Shared by buildConfigFromYaml (local-only) and initConfig (merged local+DB).
 */
function buildConfigFromMergedYaml(yaml: YamlAppConfig): { database: DatabaseConfig; app: AppConfig } {
  // ── Database config (existing logic, preserved) ──
  const dbSection = yaml.database ?? {};
  const zdasSection = dbSection.zdas ?? {};
  const datasource = zdasSection.datasources?.[0];

  const envMode = getEnv("DATABASE_MODE") as "sqlite" | "prod" | undefined;
  const mode: "sqlite" | "prod" = envMode ?? dbSection.mode ?? "sqlite";

  const sqlitePathRaw =
    getEnv("SQLITE_PATH") ?? dbSection.sqlite?.path ?? join(homedir(), ".openclaw", "workflow", "engine.db");
  const sqlitePath = sqlitePathRaw.replace(/^~/, homedir());

  let database: DatabaseConfig;

  if (mode === "sqlite") {
    database = { type: "sqlite", sqlitePath };
  } else {
    const mysqlConfig: MySqlConfig = {
      host: getEnv("ZDAS_HOST") ?? datasource?.host ?? "127.0.0.1",
      port: envInt("ZDAS_PORT", Number(datasource?.port ?? 11306)),
      user: getEnv("ZDAS_USER") ?? datasource?.user ?? "",
      password: getEnv("ZDAS_PASSWORD") ?? datasource?.password ?? "",
      database: getEnv("ZDAS_DATABASE") ?? datasource?.database ?? "",
      poolSize: envInt("ZDAS_POOL_SIZE", datasource?.pool_size ?? 10),
      poolMin: envInt("ZDAS_POOL_MIN", datasource?.pool_min ?? 5),
    };
    database = { type: "mysql", sqlitePath, mysql: mysqlConfig };
  }

  // ── Application capability configs ──
  const app: AppConfig = {
    appName: yaml.app_name ?? defaults.appName,
    version: envString("CLAWMIND_VERSION", yaml.version ?? defaults.version),
    engine: getEnv("CLAWMIND_ENGINE") ?? yaml.engine ?? undefined,
    statePersistence: parseStatePersistence(yaml.statePersistence),
    knowledge: parseKnowledge(yaml.knowledge),
    retry: parseRetry(yaml.retry),
    analysis: parseAnalysis(yaml.analysis),
    alerting: parseAlerting(yaml.alerting),
    api: parseApi(yaml.api, dbSection.api),
    scheduler: parseScheduler(yaml.scheduler),
    webhook: parseWebhook(yaml.webhook),
    contextCompression: parseContextCompression(yaml.contextCompression),
    sessionCompression: parseSessionCompression(yaml.sessionCompression),
    flowControl: loadFlowControlConfig(yaml.flowControl as Record<string, unknown> | undefined),
    llm: parseLlm(yaml.llm),
    execution: parseExecution(yaml.execution),
    nlInteraction: parseNlInteraction(yaml.nlInteraction),
    teclaw: parseTeClaw(yaml.teclaw),
    chatInject: parseChatInject(yaml.chatInject),
    asyncCallback: parseAsyncCallback(yaml.asyncCallback as Record<string, unknown> | undefined),
    git: {
      remoteUrl: yaml.git?.remoteUrl ?? "",
      username: yaml.git?.username ?? "",
      token: getEnv("CLAWMIND_GIT_TOKEN") ?? yaml.git?.token ?? "",
      email: getEnv("CLAWMIND_GIT_EMAIL") ?? yaml.git?.email ?? "",
    },
    guardian: {
      enabled: yaml.guardian?.enabled !== false,
      analysisTimeoutSeconds: yaml.guardian?.analysisTimeoutSeconds ?? 60,
      maxPromptMultiplier: yaml.guardian?.maxPromptMultiplier ?? 2,
    },
  };

  // ── Diagnostics: log version and parsed configs (stderr only — stdout is
  //    captured by Claude Code's SessionStart hook and injected as LLM context;
  //    config diagnostics MUST NOT pollute that context) ──
  console.error(`[config] clawmind v${app.version}`);

  // ── Diagnostics: log parsed compression configs ──
  console.error(
    `[config] contextCompression loaded: enabled=${app.contextCompression.enabled}, ` +
    `defaultMaxTokens=${app.contextCompression.defaultMaxTokens}, ` +
    `warningThresholdRatio=${app.contextCompression.warningThresholdRatio}, ` +
    `defaultOverflowStrategy=${app.contextCompression.defaultOverflowStrategy}, ` +
    `defaultSteps=[${app.contextCompression.defaultSteps.map((s) => s.strategy).join(",")}]`,
  );
  console.error(
    `[config] sessionCompression loaded: maxSessionTokens=${app.sessionCompression.maxSessionTokens}, ` +
    `minTokensToCompact=${app.sessionCompression.minTokensToCompact}, ` +
    `toolResultMaxChars=${app.sessionCompression.toolResultMaxChars}, ` +
    `toolPrepassEnabled=${app.sessionCompression.toolPrepassEnabled}, ` +
    `recencyWindow=${app.sessionCompression.recencyWindow}, ` +
    `mainSessionEnabled=${app.sessionCompression.mainSessionEnabled}`,
  );
  console.error(
    `[config] llm loaded: maxConcurrentCalls=${app.llm.maxConcurrentCalls}` +
    (process.env.MAX_CONCURRENT_LLM_CALLS ? ` (overridden by env var=${process.env.MAX_CONCURRENT_LLM_CALLS})` : ""),
  );
  console.error(
    `[config] execution loaded: runTimeoutMs=${app.execution.runTimeoutMs}, asyncRun=${app.execution.asyncRun}` +
    (process.env.WORKFLOW_RUN_TIMEOUT_MS ? ` (runTimeoutMs overridden by env var=${process.env.WORKFLOW_RUN_TIMEOUT_MS})` : "") +
    (process.env.WORKFLOW_ASYNC_RUN ? ` (asyncRun overridden by env var=${process.env.WORKFLOW_ASYNC_RUN})` : ""),
  );
  console.error(
    `[config] nlInteraction loaded: enabled=${app.nlInteraction.enabled}, ` +
    `exactMatch=${app.nlInteraction.exactMatch}, hintEnabled=${app.nlInteraction.hintEnabled}` +
    (process.env.CLAWMIND_NL_INTERACTION ? ` (overridden by env var=${process.env.CLAWMIND_NL_INTERACTION})` : ""),
  );
  console.error(
    `[config] teclaw loaded: enabled=${app.teclaw.enabled}, ` +
    `wsUrl=${app.teclaw.wsUrl ? "***configured***" : "(empty)"}, ` +
    `chatInjectUrl=${app.teclaw.chatInjectUrl ? "***configured***" : "(empty)"}, ` +
    `baseUrl=${app.teclaw.baseUrl ? "***configured***" : "(empty)"}, ` +
    `agentLoopUrl=${app.teclaw.agentLoopUrl ? "***configured***" : "(empty)"}` +
    (process.env.TECLAW_WS_URL ? ` (wsUrl overridden by env var)` : "") +
    (process.env.TECLAW_BASE_URL ? ` (baseUrl overridden by env var)` : ""),
  );
  console.error(
    `[config] chatInject loaded: level=${app.chatInject.level}` +
    (process.env.CHATINJECT_VERBOSITY ? ` (legacy verbosity env=${process.env.CHATINJECT_VERBOSITY})` : "") +
    (process.env.CLAWMIND_PERFORMANCE_MODE ? ` (legacy performanceMode env=${process.env.CLAWMIND_PERFORMANCE_MODE})` : "") +
    (process.env.CLAWMIND_CHAT_INJECT_LEVEL ? ` (level env=${process.env.CLAWMIND_CHAT_INJECT_LEVEL})` : ""),
  );
  console.error(
    `[config] asyncCallback loaded: enabled=${app.asyncCallback.enabled}, ` +
    `defaultTimeout=${app.asyncCallback.defaultTimeout}, ` +
    `callbackBaseUrl=${app.asyncCallback.callbackBaseUrl || "(empty)"}, ` +
    `hmacSecret=${app.asyncCallback.defaultHmacSecret ? "(configured)" : "(empty)"}`,
  );

  return { database, app };
}

/** Get the config file path for diagnostics (without reading it). */
export function resolveConfigPath(configPath?: string): string | null {
  return findConfigFile(configPath);
}

/**
 * Initialize config from DB — asynchronously loads cm_app_config sections
 * and merges them with the local application.yaml in memory.
 *
 * Phase 1: Read local application.yaml (sync)
 * Phase 2: Load remote config sections from DB or clawweb API (async)
 * Phase 3: deepMerge(local, remote) — DB overrides local for matching keys
 * Phase 4: Build AppConfig from merged YAML + env vars, cache result
 *
 * The local application.yaml file is NEVER modified — all merging is in-memory.
 *
 * Call this once after createDatabase() succeeds, before the engine starts
 * processing workflows.
 */
export async function initConfig(
  db: IDatabase,
  apiClient?: ApiClient,
  configPath?: string,
): Promise<void> {
  // Phase 1: Read local application.yaml
  const localYaml: YamlAppConfig = deepMerge<YamlAppConfig>(
    readYamlConfig(configPath),
    readOpenClawPluginConfig({ useDefaultPath: !configPath }),
  ) ?? {};

  // Phase 2: Load remote config sections from DB / API
  const remoteYaml = await loadAppConfigFromDB(db, apiClient);

  // Phase 3: Merge — DB overrides local for matching top-level keys
  const mergedYaml = deepMerge<YamlAppConfig>(localYaml, remoteYaml as YamlAppConfig) ?? localYaml;

  // Log which sections were overridden by DB
  const remoteKeys = Object.keys(remoteYaml);
  if (remoteKeys.length > 0) {
    console.error(`[config] DB cm_app_config loaded: ${remoteKeys.length} sections (${remoteKeys.join(", ")}) — overriding local application.yaml for matching keys`);
  } else {
    console.error("[config] DB cm_app_config: no enabled sections found — using local application.yaml only");
  }

  // Phase 4: Build AppConfig from merged YAML + env vars, cache result
  const result = buildConfigFromMergedYaml(mergedYaml);
  setCachedConfig(result);
}
