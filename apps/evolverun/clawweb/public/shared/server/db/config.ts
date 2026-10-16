/** ClawWeb YAML and environment configuration shared by database setup and runtime resolvers. */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { parse as parseYaml } from "yaml";

// ── YAML Config Types ──

type YamlDatasource = {
  database?: string;
  user?: string;
  password?: string;
  host?: string;
  port?: string | number;
  pool_size?: number;
  pool_min?: number;
};

type YamlDatabaseConfig = {
  mode?: "sqlite" | "prod";
  sqlite?: { path?: string };
  zdas?: {
    enabled?: boolean;
    datasources?: YamlDatasource[];
  };
};

type YamlSignatureConfig = {
  publicKeyB64?: string;
};

type YamlLangfuseConfig = {
  host?: string;
  publicKey?: string;
  secretKey?: string;
};

type YamlAnalysisConfig = {
  apiUrl?: string;
  apiKey?: string;
  model?: string;
};

type YamlFlowControlConfig = {
  /** Master switch for flow control. When false, all flow-control API requests return a disabled message without touching the database. Default: true */
  enabled?: boolean;
  queueGuard?: {
    enabled?: boolean;
    maxQueueSize?: number;
    maxEnqueuePerFlow?: number;
    activeStatuses?: string[];
  };
};

type YamlDimaConfig = {
  apiEndpoint?: string;
  accessKey?: string;
  secretKey?: string;
  staffId?: string;
  tenant?: string;
  defaultWorkspaceId?: string;
  defaultProjectId?: string;
  syncIntervalMinutes?: number;
  syncEnabled?: boolean;
  autoTriggerWorkflow?: boolean;
  typeTemplateMapping?: Record<string, string>;
  completedStatusId?: string;
};

type YamlDevWorkflowConfig = {
  defaultBotId?: string;
  defaultGitTargetBranch?: string;
  workflowTimeoutHours?: number;
  phaseTimeoutBufferMinutes?: number;
  yuqueBookId?: number;
  yuqueToken?: string;
  yuqueApiBaseUrl?: string;
  yuqueKnowledgeBases?: Array<{ bookId: number; name: string; token: string }>;
  gitPlatform?: string;
  gitApiBaseUrl?: string;
  gitApiToken?: string;
  baasEndpoint?: string;
  baasApiKey?: string;
};

type YamlAntLogsSource = {
  name: string;
  region: string;
  app: string;
  tenant: string;
  /** Override baseUrl for this source (e.g. configured log service for a configured log source) */
  baseUrl?: string;
  defaultLogstore?: string;
  defaultQuery?: string;
  defaultEnabled?: boolean;
};

export type YamlAntLogsConfig = {
  apiId?: string;
  apiKey?: string;
  baseUrl?: string;
  requestTimeoutMs?: number;
  maxBatonRounds?: number;
  sources?: YamlAntLogsSource[];
};

type YamlLogAnalysisConfig = {
  enabled?: boolean;
  cron?: string;
  logSource?: string;
  lookbackMinutes?: number;
  minErrorCount?: number;
  cooldownMinutes?: number;
  analysisApiUrl?: string;
  analysisApiKey?: string;
  analysisModel?: string;
  // ClawFix pipeline settings
  ocbModuleOwners?: Record<string, string>;
  dimaWorkspaceId?: string;
  dimaProjectId?: string;
  /** BaaS Bot ID for log collection via BaaS API (replaces MCP) */
  baasLogBotId?: string;
  /** AntLogs Direct API configuration (bypasses BaaS/MCP) */
  antlogs?: YamlAntLogsConfig;
};

type YamlAppConfig = {
  database?: YamlDatabaseConfig;
  signature?: YamlSignatureConfig;
  langfuse?: YamlLangfuseConfig;
  analysis?: YamlAnalysisConfig;
  approval?: Record<string, unknown>;
  dingtalk?: {
    appKey?: string;
    appSecret?: string;
    corpId?: string;
    robotCode?: string;
  };
  baas?: YamlBaasConfig;
  repair?: YamlRepairConfig;
  autoHeal?: YamlAutoHealConfig;
  smartOnboarding?: YamlSmartOnboardingConfig;
  flowControl?: YamlFlowControlConfig;
  dima?: YamlDimaConfig;
  devWorkflow?: YamlDevWorkflowConfig;
  logAnalysis?: YamlLogAnalysisConfig;
  auth?: {
    admins?: string[];
    log_admins?: string[];
    bench_admins?: string[];
    claw_evolve_admins?: string[];
    claw_insight_admins?: string[];
  };
};

let configuredRuntimeConfig: YamlAppConfig | null = null;

/**
 * Register the application configuration resolved by the ClawWeb composition root.
 * Module packages consume this in-memory object and do not discover configuration
 * files relative to their own cwd.
 */
export function configureClawWebRuntimeConfig(config: Record<string, unknown>): void {
  configuredRuntimeConfig = config as YamlAppConfig;
}

type YamlBaasConfig = {
  apiKey?: string;
  iamtoken?: string;
  baseUrl?: string;
  environments?: {
    dev?: YamlBaasEnvironmentConfig;
    pre?: YamlBaasEnvironmentConfig;
    prod?: YamlBaasEnvironmentConfig;
  };
  evolveScriptPaths?: {
    dev?: string;
    pre?: string;
    prod?: string;
  };
  commandTenant?: string;
  commandTimeoutSeconds?: number;
};

type YamlBaasEnvironmentConfig = {
  apiKey?: string;
  baseUrl?: string;
};

type YamlRepairConfig = {
  execution?: {
    decisionGraceSeconds?: number;
    contextWaitSeconds?: number;
    leaseSeconds?: number;
    heartbeatSeconds?: number;
    agentTimeoutSeconds?: number;
    agentCloseoutTimeoutSeconds?: number;
    maxAgentAutoRecoveries?: number;
    agentCorrectionTimeoutSeconds?: number;
    maxAgentOutputCorrectionRetries?: number;
    maxAgentRateLimitRetries?: number;
    agentRateLimitRetryBaseSeconds?: number;
  };
  requestTimeoutMs?: number;
};

type YamlAutoHealConfig = {
  botId?: string;
  diagnosisPrompt?: string;
  pollTimeoutMs?: number;
  pollIntervalMs?: number;
  /** Git remote URL for clawflows (the repo that owns packs and deploy tags). */
  clawflowsRemoteUrl?: string;
  /** Git username for push authentication. */
  gitUsername?: string;
  /** Git author name for commits. */
  gitAuthorName?: string;
  /** Git author email for commits. */
  gitAuthorEmail?: string;
  /** Git auth token/password for push authentication (env > yaml). */
  gitToken?: string;
};

type YamlSmartOnboardingConfig = {
  defaultBotId?: string;
  generatePrompt?: string;
  generateTimeoutMs?: number;
  testRunTimeoutMs?: number;
  maxYamlLength?: number;
  maxPromptLength?: number;
};

// ── Config Discovery ──

const CONFIG_FILENAMES = ["application.yaml", "application.yml"];

function findPackageRoot(): string | null {
  let dir = import.meta.dirname ?? process.cwd();
  for (let i = 0; i < 20; i++) {
    if (existsSync(join(dir, "package.json"))) return dir;
    const parent = join(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

function findConfigFile(explicitPath?: string): string | null {
  if (explicitPath && existsSync(explicitPath)) {
    return explicitPath;
  }

  const pkgRoot = findPackageRoot();
  const searchDirs = [
    process.cwd(),
    join(process.cwd(), "configs"),
    pkgRoot,
    pkgRoot ? join(pkgRoot, "configs") : null,
  ].filter(Boolean) as string[];

  for (const dir of searchDirs) {
    for (const filename of CONFIG_FILENAMES) {
      const candidate = join(dir, filename);
      if (existsSync(candidate)) {
        return candidate;
      }
    }
  }

  return null;
}

export function readYamlConfig(configPath?: string): YamlAppConfig {
  if (!configPath && configuredRuntimeConfig) return configuredRuntimeConfig;
  const filePath = findConfigFile(configPath);
  if (!filePath) return {};

  try {
    const raw = readFileSync(filePath, "utf-8");
    return parseYaml(raw) as YamlAppConfig;
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[clawweb] Failed to read config file ${filePath}: ${msg}, using defaults`);
    return {};
  }
}

// ── Env Var Helpers ──

export function getEnv(key: string): string | undefined {
  return process.env[key];
}

export function envInt(key: string, fallback: number): number {
  const raw = getEnv(key);
  if (!raw) return fallback;
  const parsed = parseInt(raw, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

export function repairEnvInteger(key: string, fallback: number): number {
  const raw = getEnv(key);
  if (!raw) return fallback;
  const normalized = raw.trim();
  return /^[+-]?\d+$/.test(normalized) ? Number(normalized) : Number.NaN;
}

// ── Resolved Config ──

export type ResolvedDbConfig = {
  mode: "sqlite" | "mysql" | "zdas" | "prod";
  sqlitePath: string;
  mysql: {
    host: string;
    port: number;
    user: string;
    password: string;
    database: string;
    poolSize: number;
    poolMin: number;
  };
  configSource: string | null;
};

export function resolveDbConfig(configPath?: string): ResolvedDbConfig {
  const yaml = readYamlConfig(configPath);
  const dbSection = yaml.database ?? {};
  const datasource = dbSection.zdas?.datasources?.[0];

  // Config source for diagnostics
  const configSource = !configPath && configuredRuntimeConfig
    ? "composition-root"
    : findConfigFile(configPath);

  // Mode: env var > yaml > default "sqlite"
  const envMode = getEnv("DATABASE_MODE") as "sqlite" | "mysql" | "zdas" | "prod" | undefined;
  const rawMode = envMode ?? dbSection.mode ?? "sqlite";

  // Backward compatibility: legacy "prod" maps to "zdas".
  const mode: ResolvedDbConfig["mode"] = rawMode === "prod" ? "zdas" : rawMode;
  if (rawMode === "prod") {
    console.warn('[clawweb] DATABASE_MODE="prod" is deprecated; please use "zdas" or "mysql" explicitly');
  }

  // SQLite path: env var > yaml > default
  const sqlitePathRaw =
    getEnv("SQLITE_PATH") ?? dbSection.sqlite?.path ?? join(homedir(), ".openclaw", "workflow", "engine.db");
  const sqlitePath = sqlitePathRaw.replace(/^~/, homedir());

  // MySQL/ZDAS: env vars > yaml datasource > defaults
  const mysql = {
    host: getEnv("ZDAS_HOST") ?? datasource?.host ?? "127.0.0.1",
    port: envInt("ZDAS_PORT", Number(datasource?.port ?? 11306)),
    user: getEnv("ZDAS_USER") ?? datasource?.user ?? "",
    password: getEnv("ZDAS_PASSWORD") ?? datasource?.password ?? "",
    database: getEnv("ZDAS_DATABASE") ?? datasource?.database ?? "",
    poolSize: envInt("ZDAS_POOL_SIZE", datasource?.pool_size ?? 10),
    poolMin: envInt("ZDAS_POOL_MIN", datasource?.pool_min ?? 5),
  };

  return { mode, sqlitePath, mysql, configSource };
}
