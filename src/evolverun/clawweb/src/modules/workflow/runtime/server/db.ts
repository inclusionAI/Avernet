/**
 * Standalone database initialization for ClawWeb.
 * Reads DB config from configs/application.yaml (aligned with ClawFlow),
 * with environment variable overrides.
 * Uses better-sqlite3 (local) and mysql2 (ZDAS/prod).
 * NO dependency on ClawFlow — shares the same database tables only.
 */
import Database from "better-sqlite3";
import mysql from "mysql2/promise";
import { existsSync, readFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { parse as parseYaml } from "yaml";
import { getCurrentEnv } from "./env.js";
import { migrations, sqliteTriggers, type DbType } from "./schema.js";
import { dialectFor, sqliteDialect, type Dialect } from "./db/dialect.js";

// ── Types ──

export type Row = Record<string, unknown>;

// ── DB-aware helpers ──

export function nowForDb(dbType: "sqlite" | "mysql" | "zdas" | "noop"): number | string {
  if (dbType === "mysql" || dbType === "zdas") {
    const d = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
  return Math.floor(Date.now() / 1000);
}

/**
 * Convert a unix-seconds epoch value to the DB-appropriate timestamp format.
 * On MySQL/ZDAS: returns 'YYYY-MM-DD HH:MM:SS' for TIMESTAMP columns.
 * On SQLite: returns the raw unix seconds for INTEGER columns.
 * Returns null if the input is null/undefined.
 */
export function epochToDb(
  dbType: "sqlite" | "mysql" | "zdas" | "noop",
  epochSec: number | null | undefined,
): number | string | null {
  if (epochSec == null) return null;
  if (dbType !== "mysql" && dbType !== "zdas") return epochSec;
  const d = new Date(epochSec * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export type ExecResult = {
  affectedRows: number;
  insertId?: number;
};

export interface IDatabase {
  readonly dbType: "sqlite" | "mysql" | "zdas" | "noop";
  readonly dialect: Dialect;
  query<T = Row>(sql: string, params?: unknown[]): Promise<T[]>;
  exec(sql: string, params?: unknown[]): Promise<ExecResult>;
  transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T>;
  close(): Promise<void>;
}

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
  /** Override baseUrl for this source (e.g. antlogs.alipay.com for BCN/secbaas) */
  baseUrl?: string;
  defaultLogstore?: string;
  defaultQuery?: string;
  defaultEnabled?: boolean;
};

type YamlAntLogsConfig = {
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
  };
};

type YamlBaasConfig = {
  apiKey?: string;
  iamtoken?: string;
  baseUrl?: string;
  environments?: {
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
  ais?: {
    /** Legacy fallback used when an environment-specific Snapshot is absent. */
    snapshotId?: number;
    snapshots?: {
      pre?: number;
      prod?: number;
    };
  };
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

function readYamlConfig(configPath?: string): YamlAppConfig {
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

function getEnv(key: string): string | undefined {
  return process.env[key];
}

function envInt(key: string, fallback: number): number {
  const raw = getEnv(key);
  if (!raw) return fallback;
  const parsed = parseInt(raw, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

function repairEnvInteger(key: string, fallback: number): number {
  const raw = getEnv(key);
  if (!raw) return fallback;
  const normalized = raw.trim();
  return /^[+-]?\d+$/.test(normalized) ? Number(normalized) : Number.NaN;
}

// ── Resolved Config ──

type ResolvedDbConfig = {
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

function resolveDbConfig(configPath?: string): ResolvedDbConfig {
  const yaml = readYamlConfig(configPath);
  const dbSection = yaml.database ?? {};
  const datasource = dbSection.zdas?.datasources?.[0];

  // Config source for diagnostics
  const configSource = findConfigFile(configPath);

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

// ── NoOp Database ──

class NoOpDatabase implements IDatabase {
  readonly dbType = "noop" as const;
  readonly dialect = sqliteDialect;
  async query<T>(): Promise<T[]> { return []; }
  async exec(): Promise<ExecResult> { return { affectedRows: 0 }; }
  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> { return fn(this); }
  async close(): Promise<void> {}
}

// ── SQLite Database ──

class SqliteDatabase implements IDatabase {
  readonly dbType = "sqlite" as const;
  readonly dialect = sqliteDialect;
  private db: Database.Database;

  constructor(db: Database.Database) {
    this.db = db;
  }

  async query<T>(sql: string, params?: unknown[]): Promise<T[]> {
    const stmt = this.db.prepare(sql);
    const rows = params ? stmt.all(...params) : stmt.all();
    return rows as T[];
  }

  async exec(sql: string, params?: unknown[]): Promise<ExecResult> {
    // When no parameters are supplied the SQL may contain multiple statements
    // (e.g. a ZDAS-style DDL that the dialect renders into CREATE TABLE plus
    // several CREATE INDEX statements). better-sqlite3's exec() runs the whole
    // batch, whereas prepare() only accepts a single statement.
    if (params === undefined) {
      this.db.exec(sql);
      return { affectedRows: 0, insertId: undefined };
    }
    const stmt = this.db.prepare(sql);
    const result = stmt.run(...params);
    return { affectedRows: result.changes, insertId: result.lastInsertRowid as number | undefined };
  }

  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> {
    this.db.exec("BEGIN");
    try {
      const result = await fn(this);
      this.db.exec("COMMIT");
      return result;
    } catch (err) {
      this.db.exec("ROLLBACK");
      throw err;
    }
  }

  async close(): Promise<void> {
    this.db.close();
  }
}

// ── MySQL Database ──

class MysqlDatabase implements IDatabase {
  readonly dbType: "mysql" | "zdas";
  readonly dialect: Dialect;
  private pool: mysql.Pool;

  constructor(pool: mysql.Pool, dbType: "mysql" | "zdas" = "mysql") {
    this.pool = pool;
    this.dbType = dbType;
    this.dialect = dialectFor(dbType);
  }

  async query<T>(sql: string, params?: unknown[]): Promise<T[]> {
    const [rows] = await this.pool.query(sql, params as mysql.QueryValues | undefined);
    return rows as T[];
  }

  async exec(sql: string, params?: unknown[]): Promise<ExecResult> {
    // Use pool.query() (text protocol) instead of pool.execute() (binary protocol).
    // pool.execute() creates server-side Prepared Statements that consume cursors;
    // under high concurrency with OceanBase/MySQL this exhausts open_cursors limits.
    // For one-shot write operations (INSERT/UPDATE/DELETE) there is no performance
    // benefit from prepared statements — the text protocol is just as fast and
    // does not leak cursors. See: maximum open cursors exceeded production incident.
    const [result] = await this.pool.query(sql, params as mysql.QueryValues | undefined);
    const r = result as mysql.ResultSetHeader;
    return { affectedRows: r.affectedRows, insertId: r.insertId || undefined };
  }

  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> {
    const conn = await this.pool.getConnection();
    try {
      await conn.beginTransaction();
      const txDb = new MysqlTransactionDb(conn, this.dbType);
      const result = await fn(txDb);
      await conn.commit();
      return result;
    } catch (err) {
      await conn.rollback();
      throw err;
    } finally {
      conn.release();
    }
  }

  async close(): Promise<void> {
    await this.pool.end();
  }
}

class MysqlTransactionDb implements IDatabase {
  readonly dbType: "mysql" | "zdas";
  readonly dialect: Dialect;
  private conn: mysql.PoolConnection;

  constructor(conn: mysql.PoolConnection, dbType: "mysql" | "zdas" = "mysql") {
    this.conn = conn;
    this.dbType = dbType;
    this.dialect = dialectFor(dbType);
  }

  async query<T>(sql: string, params?: unknown[]): Promise<T[]> {
    const [rows] = await this.conn.query(sql, params as mysql.QueryValues | undefined);
    return rows as T[];
  }

  async exec(sql: string, params?: unknown[]): Promise<ExecResult> {
    // Use conn.query() instead of conn.execute() to avoid server-side Prepared
    // Statement cursor leaks on OceanBase/MySQL. Same rationale as MysqlDatabase.exec().
    const [result] = await this.conn.query(sql, params as mysql.QueryValues | undefined);
    const r = result as mysql.ResultSetHeader | null;
    if (!r) {
      return { affectedRows: 0 };
    }
    return { affectedRows: r.affectedRows, insertId: r.insertId || undefined };
  }

  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> {
    return fn(this);
  }

  async close(): Promise<void> {}
}

// ── Migration Runner ──

async function getSchemaVersion(db: IDatabase): Promise<number> {
  try {
    const rows = await db.query<{ v: number }>(
      "SELECT MAX(version) as v FROM schema_version"
    );
    return rows[0]?.v ?? 0;
  } catch {
    return 0;
  }
}

async function ensureSchemaVersionTable(db: IDatabase): Promise<boolean> {
  try {
    await db.exec(
      db.dialect.renderDdl(
        `CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL, description TEXT, gmt_create INTEGER NOT NULL DEFAULT (unixepoch()), gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()))`
      )
    );
    return true;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("denied") || msg.includes("CREATE") || msg.includes("DDL")) {
      console.warn(`[clawweb] DDL denied (assumes schema exists in managed DB): ${msg}`);
      return false;
    }
    throw err;
  }
}

async function runMigrations(db: IDatabase, dbType: DbType): Promise<void> {
  const canDdl = await ensureSchemaVersionTable(db);
  if (!canDdl) {
    console.warn("[clawweb] DDL denied — skipping migrations, assuming schema exists in managed DB");
    return;
  }

  const current = await getSchemaVersion(db);

  for (const migration of migrations) {
    if (migration.version <= current) continue;
    if (migration.sqliteOnly && dbType !== "sqlite") continue;
    if (migration.mysqlOnly && dbType !== "mysql" && dbType !== "zdas") continue;
    for (const ddl of migration.sql) {
      try {
        await db.exec(db.dialect.renderDdl(ddl));
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        const lower = msg.toLowerCase();
        // ALTER TABLE ADD COLUMN fails if column already exists — safe to skip.
        // SQLite: "duplicate column name"; MySQL/OceanBase: "Duplicate column name".
        // Also tolerate the occasional "already exists" / "key column ... does not exist".
        // MySQL error 1091: "Can't DROP column" — column doesn't exist (fresh install).
        if (
          lower.includes("duplicate column name") ||
          lower.includes("no such column") ||
          lower.includes("already exists") ||
          lower.includes("unknown error 1091") ||
          lower.includes("can't drop")
        ) {
          console.warn(`[clawweb] Migration v${migration.version} skipped DDL (already applied): ${msg}`);
        } else {
          throw error;
        }
      }
    }
    await db.exec(
      "INSERT INTO schema_version (version, description) VALUES (?, ?)",
      [migration.version, migration.description]
    );
    console.log(`[clawweb] Migration v${migration.version}: ${migration.description}`);
  }

  // Apply SQLite AFTER UPDATE triggers for gmt_modified auto-update
  if (dbType === "sqlite") {
    for (const triggerDdl of sqliteTriggers) {
      try {
        await db.exec(triggerDdl);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.warn(`[clawweb] SQLite trigger creation failed: ${msg}`);
      }
    }
  }
}

// ── Public API ──

export type Repositories = {
  db: IDatabase;
};

let repos: Repositories | null = null;

export async function initDatabase(configPath?: string): Promise<Repositories> {
  if (repos) return repos;

  const config = resolveDbConfig(configPath);

  if (config.configSource) {
    console.log(`[clawweb] Loaded config from ${config.configSource} (mode=${config.mode})`);
  } else {
    console.log("[clawweb] No application.yaml found, using defaults");
  }

  let db: IDatabase;
  try {
    if (config.mode === "mysql" || config.mode === "zdas") {
      db = await initMysql(config.mysql, config.mode);
    } else {
      db = initSqlite(config.sqlitePath);
    }

    await runMigrations(db, db.dbType as DbType);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`[clawweb] Database init failed (mode=${config.mode}): ${msg}`);
    console.warn("[clawweb] Falling back to NoOp database — API will return 503");
    db = new NoOpDatabase();
  }

  repos = { db };
  console.log(`[clawweb] Database ready (type=${db.dbType})`);
  return repos;
}

/** Resolve signature config from env var > yaml. */
export function resolveSignatureConfig(configPath?: string): { publicKeyB64: string } {
  const yaml = readYamlConfig(configPath);
  const publicKeyB64 = getEnv("CLAWWEB_PUBLIC_KEY") ?? yaml.signature?.publicKeyB64 ?? "";
  return { publicKeyB64 };
}

/** Resolve Langfuse config from env var > yaml. */
export function resolveLangfuseConfig(configPath?: string): { publicKey: string; secretKey: string; host: string } {
  const yaml = readYamlConfig(configPath);
  return {
    publicKey: getEnv("LANGFUSE_PUBLIC_KEY") ?? yaml.langfuse?.publicKey ?? "",
    secretKey: getEnv("LANGFUSE_SECRET_KEY") ?? yaml.langfuse?.secretKey ?? "",
    host: getEnv("LANGFUSE_HOST") ?? yaml.langfuse?.host ?? "https://cloud.langfuse.com",
  };
}

/** Resolve LLM analysis config from env var > yaml. */
export function resolveAnalysisConfig(configPath?: string): { apiUrl: string; apiKey: string; model: string } {
  const yaml = readYamlConfig(configPath);
  return {
    apiUrl: getEnv("LLM_API_URL") ?? yaml.analysis?.apiUrl ?? "https://antchat.alipay.com/api/anthropic",
    apiKey: getEnv("LLM_API_KEY") ?? yaml.analysis?.apiKey ?? "",
    model: getEnv("LLM_MODEL") ?? yaml.analysis?.model ?? "GLM-5.1",
  };
}

export type DingTalkBotConfig = {
  appKey: string;
  appSecret: string;
  robotCode: string;
  apiBaseUrl: string;
  publicBaseUrl: string;
};

export type InsightHandoffConfig = {
  publicBaseUrl: string;
};

function resolveClawWebPublicBaseUrl(): string {
  const override = getEnv("CLAWWEB_PUBLIC_BASE_URL");
  if (override) return override.replace(/\/$/, "");

  const configuredRuntimeMode = getEnv("RUNTIME_MODE")?.trim().toLowerCase();
  const runtimeMode = configuredRuntimeMode
    ? configuredRuntimeMode === "pre" || configuredRuntimeMode === "prepub"
      ? "pre"
      : configuredRuntimeMode === "prod" || configuredRuntimeMode === "gray"
        ? "prod"
        : "dev"
    : getCurrentEnv();

  return runtimeMode === "pre"
    ? "https://clawweb-pre.alipay.com"
    : runtimeMode === "prod"
      ? "https://clawweb.alipay.com"
      : "http://localhost:5173";
}

function firstNonBlank(...values: Array<string | undefined>): string {
  return values.find((value) => value?.trim())?.trim() ?? "";
}

/**
 * Resolve the DingTalk robot sender configuration.
 *
 * Improvement notifications use the packaged ClawWeb robot by default. ACI
 * frequently injects declared variables as empty strings, so empty or partial
 * DINGTALK_BOT_* values must never disable or mix credentials with that default.
 * Generic DINGTALK_APP_* variables belong to DingTalk login and are deliberately
 * ignored here. A complete bot-specific appKey/appSecret pair may override the
 * packaged channel; robotCode defaults to that app's Client ID.
 */
export function resolveDingTalkBotConfig(configPath?: string): DingTalkBotConfig {
  const yaml = readYamlConfig(configPath);
  const defaultAppKey = firstNonBlank(yaml.dingtalk?.appKey);
  const defaultAppSecret = firstNonBlank(yaml.dingtalk?.appSecret);
  const botAppKey = firstNonBlank(getEnv("DINGTALK_BOT_APP_KEY"));
  const botAppSecret = firstNonBlank(getEnv("DINGTALK_BOT_APP_SECRET"));
  const useBotOverride = Boolean(botAppKey && botAppSecret);
  const appKey = useBotOverride ? botAppKey : defaultAppKey;
  const appSecret = useBotOverride ? botAppSecret : defaultAppSecret;
  const robotCode = useBotOverride
    ? firstNonBlank(getEnv("DINGTALK_BOT_ROBOT_CODE"), botAppKey)
    : firstNonBlank(yaml.dingtalk?.robotCode, defaultAppKey);

  return {
    appKey,
    appSecret,
    robotCode,
    apiBaseUrl: firstNonBlank(getEnv("DINGTALK_API_BASE_URL"), "https://api.dingtalk.com").replace(/\/$/, ""),
    publicBaseUrl: resolveClawWebPublicBaseUrl(),
  };
}

/** Resolve self-repair handoff configuration. Evidence is exposed through an internal read-only URL. */
export function resolveInsightHandoffConfig(): InsightHandoffConfig {
  return {
    publicBaseUrl: resolveClawWebPublicBaseUrl(),
  };
}

export type ResolvedBaasEnvironmentConfig = {
  apiKey: string;
  baseUrl: string;
};

export type ResolvedBaasConfig = {
  apiKey: string; iamtoken: string; baseUrl: string;
  environments: Record<"pre" | "prod", ResolvedBaasEnvironmentConfig>;
  evolveScriptPaths: Record<"dev" | "pre" | "prod", string>;
  commandTenant: string; commandTimeoutSeconds: number;
};

/**
 * Resolve the existing BaaS client plus explicit environment endpoints.
 *
 * Generic callers keep using the production-compatible top-level values.
 * Evolve dispatch selects a complete endpoint + credential pair from the
 * target Bot runtime environment and never shares credentials across envs.
 */
export function resolveBaasConfig(configPath?: string): ResolvedBaasConfig {
  const yaml = readYamlConfig(configPath);
  const legacyApiKey = yaml.baas?.apiKey ?? "";
  const legacyBaseUrl = yaml.baas?.baseUrl ?? "";
  const evolveScriptPaths = {
    dev: yaml.baas?.evolveScriptPaths?.dev?.trim() ?? "",
    pre: yaml.baas?.evolveScriptPaths?.pre?.trim() ?? "",
    prod: yaml.baas?.evolveScriptPaths?.prod?.trim() ?? "",
  };
  if (!evolveScriptPaths.dev || !evolveScriptPaths.pre || !evolveScriptPaths.prod) {
    throw new Error("BaaS evolveScriptPaths.dev/pre/prod 必须在 YAML 中显式配置");
  }
  const environments: Record<"pre" | "prod", ResolvedBaasEnvironmentConfig> = {
    pre: {
      apiKey: yaml.baas?.environments?.pre?.apiKey ?? "",
      baseUrl: firstNonBlank(
        yaml.baas?.environments?.pre?.baseUrl,
        "https://secbaas-pre.alipay.com",
      ).replace(/\/$/, ""),
    },
    prod: {
      apiKey: firstNonBlank(yaml.baas?.environments?.prod?.apiKey, legacyApiKey),
      baseUrl: firstNonBlank(yaml.baas?.environments?.prod?.baseUrl, legacyBaseUrl)
        .replace(/\/$/, ""),
    },
  };
  return {
    // Preserve existing non-Evolve callers; they historically use production BaaS.
    apiKey: environments.prod.apiKey,
    iamtoken: yaml.baas?.iamtoken ?? "",
    baseUrl: environments.prod.baseUrl,
    environments,
    evolveScriptPaths,
    commandTenant: yaml.baas?.commandTenant ?? "team_claw",
    commandTimeoutSeconds: yaml.baas?.commandTimeoutSeconds ?? 30,
  };
}

export type ResolvedRepairServiceConfig = {
  aisSnapshotIds: Record<"pre" | "prod", number>;
  decisionGraceSeconds: number;
  contextWaitSeconds: number;
  executionLeaseSeconds: number;
  heartbeatIntervalSeconds: number;
  agentTimeoutSeconds: number;
  agentCloseoutTimeoutSeconds: number;
  maxAgentAutoRecoveries: number;
  agentCorrectionTimeoutSeconds: number;
  maxAgentOutputCorrectionRetries: number;
  maxAgentRateLimitRetries: number;
  agentRateLimitRetryBaseSeconds: number;
  requestTimeoutMs: number;
};

/** Resolve Repair-only integration gaps through ClawWeb's central YAML config. */
export function resolveRepairServiceConfig(configPath?: string): ResolvedRepairServiceConfig {
  const yaml = readYamlConfig(configPath).repair;
  const legacyAisSnapshotId = envInt(
    "REPAIR_AIS_SNAPSHOT_ID",
    yaml?.ais?.snapshotId ?? 62_380_375,
  );
  const contextWaitSeconds = envInt(
    "REPAIR_CONTEXT_WAIT_SECONDS",
    yaml?.execution?.contextWaitSeconds ?? 30 * 60,
  );
  const legacyCompatibleAgentTimeout = Math.max(15 * 60, contextWaitSeconds + 5 * 60);
  return {
    aisSnapshotIds: {
      pre: envInt(
        "REPAIR_AIS_PRE_SNAPSHOT_ID",
        yaml?.ais?.snapshots?.pre ?? legacyAisSnapshotId,
      ),
      prod: envInt(
        "REPAIR_AIS_PROD_SNAPSHOT_ID",
        yaml?.ais?.snapshots?.prod ?? legacyAisSnapshotId,
      ),
    },
    decisionGraceSeconds: envInt(
      "REPAIR_DECISION_GRACE_SECONDS",
      yaml?.execution?.decisionGraceSeconds ?? 15 * 60,
    ),
    contextWaitSeconds,
    executionLeaseSeconds: envInt(
      "REPAIR_EXECUTION_LEASE_SECONDS",
      yaml?.execution?.leaseSeconds ?? 90,
    ),
    heartbeatIntervalSeconds: envInt(
      "REPAIR_HEARTBEAT_INTERVAL_SECONDS",
      yaml?.execution?.heartbeatSeconds ?? 30,
    ),
    agentTimeoutSeconds: repairEnvInteger(
      "REPAIR_AGENT_TIMEOUT_SECONDS",
      yaml?.execution?.agentTimeoutSeconds ?? legacyCompatibleAgentTimeout,
    ),
    agentCloseoutTimeoutSeconds: repairEnvInteger(
      "REPAIR_AGENT_CLOSEOUT_TIMEOUT_SECONDS",
      yaml?.execution?.agentCloseoutTimeoutSeconds ?? 3 * 60,
    ),
    maxAgentAutoRecoveries: repairEnvInteger(
      "REPAIR_MAX_AGENT_AUTO_RECOVERIES",
      yaml?.execution?.maxAgentAutoRecoveries ?? 2,
    ),
    agentCorrectionTimeoutSeconds: repairEnvInteger(
      "REPAIR_AGENT_CORRECTION_TIMEOUT_SECONDS",
      yaml?.execution?.agentCorrectionTimeoutSeconds ?? 2 * 60,
    ),
    maxAgentOutputCorrectionRetries: repairEnvInteger(
      "REPAIR_MAX_AGENT_OUTPUT_CORRECTION_RETRIES",
      yaml?.execution?.maxAgentOutputCorrectionRetries ?? 3,
    ),
    maxAgentRateLimitRetries: repairEnvInteger(
      "REPAIR_MAX_AGENT_RATE_LIMIT_RETRIES",
      yaml?.execution?.maxAgentRateLimitRetries ?? 3,
    ),
    agentRateLimitRetryBaseSeconds: repairEnvInteger(
      "REPAIR_AGENT_RATE_LIMIT_RETRY_BASE_SECONDS",
      yaml?.execution?.agentRateLimitRetryBaseSeconds ?? 5,
    ),
    requestTimeoutMs: envInt("REPAIR_REQUEST_TIMEOUT_MS", yaml?.requestTimeoutMs ?? 15_000),
  };
}

/** Resolve auto-heal config from env var > yaml. */
export function resolveAutoHealConfig(configPath?: string): { botId: string; diagnosisPrompt: string; pollTimeoutMs: number; pollIntervalMs: number } {
  const yaml = readYamlConfig(configPath);
  return {
    botId: getEnv("AUTO_HEAL_BOT_ID") ?? yaml.autoHeal?.botId ?? "",
    diagnosisPrompt: getEnv("AUTO_HEAL_DIAGNOSIS_PROMPT") ?? yaml.autoHeal?.diagnosisPrompt ?? "",
    pollTimeoutMs: envInt("AUTO_HEAL_POLL_TIMEOUT_MS", yaml.autoHeal?.pollTimeoutMs ?? 1800000),
    pollIntervalMs: envInt("AUTO_HEAL_POLL_INTERVAL_MS", yaml.autoHeal?.pollIntervalMs ?? 3000),
  };
}

/** Resolve smart-onboarding config from env var > yaml. */
export function resolveSmartOnboardingConfig(configPath?: string): { defaultBotId: string; generatePrompt: string; generateTimeoutMs: number; testRunTimeoutMs: number; maxYamlLength: number; maxPromptLength: number } {
  const yaml = readYamlConfig(configPath);
  return {
    defaultBotId: getEnv("SMART_ONBOARDING_BOT_ID") ?? yaml.smartOnboarding?.defaultBotId ?? "",
    generatePrompt: getEnv("SMART_ONBOARDING_GENERATE_PROMPT") ?? yaml.smartOnboarding?.generatePrompt ?? "",
    generateTimeoutMs: envInt("SMART_ONBOARDING_GENERATE_TIMEOUT_MS", yaml.smartOnboarding?.generateTimeoutMs ?? 300000),
    testRunTimeoutMs: envInt("SMART_ONBOARDING_TEST_RUN_TIMEOUT_MS", yaml.smartOnboarding?.testRunTimeoutMs ?? 600000),
    maxYamlLength: envInt("SMART_ONBOARDING_MAX_YAML_LENGTH", yaml.smartOnboarding?.maxYamlLength ?? 100000),
    maxPromptLength: envInt("SMART_ONBOARDING_MAX_PROMPT_LENGTH", yaml.smartOnboarding?.maxPromptLength ?? 5000),
  };
}

export type QueueGuardConfig = {
  enabled: boolean;
  maxQueueSize: number;
  maxEnqueuePerFlow: number;
  activeStatuses: string[];
};

const QUEUE_GUARD_DEFAULTS: QueueGuardConfig = {
  enabled: true,
  maxQueueSize: 5000,
  maxEnqueuePerFlow: 3,
  activeStatuses: ["waiting"],
};

export type FlowControlAppConfig = {
  /** Master switch for flow control. When false, all flow-control API requests
   *  return a disabled message without touching the database. Default: true */
  enabled: boolean;
  queueGuard: QueueGuardConfig;
};

export function resolveFlowControlConfig(configPath?: string): FlowControlAppConfig {
  const yaml = readYamlConfig(configPath);
  const raw = yaml.flowControl;
  const enabled = typeof raw?.enabled === "boolean" ? raw.enabled : true;
  const queueGuardRaw = raw?.queueGuard;
  const queueGuard: QueueGuardConfig = !queueGuardRaw
    ? QUEUE_GUARD_DEFAULTS
    : {
        enabled: typeof queueGuardRaw.enabled === "boolean" ? queueGuardRaw.enabled : QUEUE_GUARD_DEFAULTS.enabled,
        maxQueueSize: typeof queueGuardRaw.maxQueueSize === "number" ? queueGuardRaw.maxQueueSize : QUEUE_GUARD_DEFAULTS.maxQueueSize,
        maxEnqueuePerFlow: typeof queueGuardRaw.maxEnqueuePerFlow === "number" ? queueGuardRaw.maxEnqueuePerFlow : QUEUE_GUARD_DEFAULTS.maxEnqueuePerFlow,
        activeStatuses: Array.isArray(queueGuardRaw.activeStatuses) && queueGuardRaw.activeStatuses.length > 0
          ? queueGuardRaw.activeStatuses.map(String)
          : QUEUE_GUARD_DEFAULTS.activeStatuses,
      };
  return { enabled, queueGuard };
}

/** @deprecated Use resolveFlowControlConfig() instead — it returns the full FlowControlAppConfig. */
export function resolveQueueGuardConfig(configPath?: string): QueueGuardConfig {
  return resolveFlowControlConfig(configPath).queueGuard;
}

// ── Admin Config ──

export type AdminConfig = {
  admins: Set<string>;
  logAdmins: Set<string>;
  benchAdmins: Set<string>;
  clawEvolveAdmins: Set<string>;
};

/** Resolve admin lists purely from yaml config (fallback when DB is unavailable).
 *  Values are normalized to lowercase for consistent Set lookups. */
export function resolveAdminConfig(configPath?: string): AdminConfig {
  const yaml = readYamlConfig(configPath);
  const norm = (arr: unknown) =>
    new Set((arr as string[] ?? []).filter(Boolean).map((s) => String(s).trim().toLowerCase()));
  return {
    admins: norm(yaml.auth?.admins),
    logAdmins: norm(yaml.auth?.log_admins),
    benchAdmins: norm(yaml.auth?.bench_admins),
    clawEvolveAdmins: norm(yaml.auth?.claw_evolve_admins),
  };
}

/** Resolve dynamic admin lists from the database, seeding YAML values first. */
export async function resolveDynamicAdminConfig(
  db: IDatabase,
  configPath?: string,
  createdBy?: string,
): Promise<AdminConfig> {
  const { AdminUserRepository } = await import("./repositories/admin-user-repository.js");
  const repo = new AdminUserRepository(db);

  // Seed YAML lists into DB so existing config remains authoritative during migration.
  const yaml = resolveAdminConfig(configPath);
  await repo.seedFromYaml(
    {
      admin: [...yaml.admins],
      log_admin: [...yaml.logAdmins],
      bench_admin: [...yaml.benchAdmins],
      claw_evolve_admin: [...yaml.clawEvolveAdmins],
    },
    createdBy,
  );

  return repo.listEnabled();
}

// ── Dima Config ──

export type DimaConfig = {
  apiEndpoint: string;
  accessKey: string;
  secretKey: string;
  staffId: string;
  tenant: string;
  defaultWorkspaceId: string;
  defaultProjectId: string;
  syncIntervalMinutes: number;
  syncEnabled: boolean;
  autoTriggerWorkflow: boolean;
  typeTemplateMapping: Record<string, string>;
  completedStatusId: string;
};

export function resolveDimaConfig(configPath?: string): DimaConfig {
  const yaml = readYamlConfig(configPath);
  const mappingRaw = getEnv("DIMA_TYPE_TEMPLATE_MAPPING") ?? yaml.dima?.typeTemplateMapping;
  const typeTemplateMapping = typeof mappingRaw === "object" && mappingRaw !== null
    ? Object.fromEntries(Object.entries(mappingRaw).map(([k, v]) => [k, String(v)]))
    : { issue: "standard-dev", bug: "quick-fix", task: "standard-dev" };
  return {
    apiEndpoint: getEnv("DIMA_API_ENDPOINT") ?? yaml.dima?.apiEndpoint ?? "https://devapi.alipay.com/arkcooprod/openapi",
    accessKey: getEnv("DIMA_ACCESS_KEY") ?? yaml.dima?.accessKey ?? "",
    secretKey: getEnv("DIMA_SECRET_KEY") ?? getEnv("DIMA_ACCESS_SECRET") ?? yaml.dima?.secretKey ?? "",
    staffId: getEnv("DIMA_STAFF_ID") ?? yaml.dima?.staffId ?? "",
    tenant: getEnv("DIMA_TENANT") ?? yaml.dima?.tenant ?? "alipay",
    defaultWorkspaceId: getEnv("DIMA_DEFAULT_WORKSPACE_ID") ?? yaml.dima?.defaultWorkspaceId ?? "W26001113566",
    defaultProjectId: getEnv("DIMA_DEFAULT_PROJECT_ID") ?? yaml.dima?.defaultProjectId ?? "",
    syncIntervalMinutes: envInt("DIMA_SYNC_INTERVAL_MINUTES", yaml.dima?.syncIntervalMinutes ?? 30),
    syncEnabled: getEnv("DIMA_SYNC_ENABLED") !== "false" && (yaml.dima?.syncEnabled !== false),
    autoTriggerWorkflow: getEnv("DIMA_AUTO_TRIGGER_WORKFLOW") === "true" || (yaml.dima?.autoTriggerWorkflow === true),
    typeTemplateMapping,
    completedStatusId: getEnv("DIMA_COMPLETED_STATUS_ID") ?? yaml.dima?.completedStatusId ?? "",
  };
}

// ── Dev Workflow Config ──

export type YuQueKnowledgeBase = {
  bookId: number;
  name: string;
  token: string;
};

export type DevWorkflowConfig = {
  defaultBotId: string;
  defaultGitTargetBranch: string;
  workflowTimeoutHours: number;
  phaseTimeoutBufferMinutes: number;
  yuqueBookId: number | null;
  yuqueToken: string;
  yuqueApiBaseUrl: string;
  yuqueKnowledgeBases: YuQueKnowledgeBase[];
  gitPlatform: string;
  gitApiBaseUrl: string;
  gitApiToken: string;
  baasEndpoint: string;
  baasApiKey: string;
};

export function resolveDevWorkflowConfig(configPath?: string): DevWorkflowConfig {
  const yaml = readYamlConfig(configPath);
  const yuqueBookIdRaw = getEnv("DEV_WF_YUQUE_BOOK_ID") ?? yaml.devWorkflow?.yuqueBookId;
  return {
    defaultBotId: getEnv("DEV_WF_DEFAULT_BOT_ID") ?? yaml.devWorkflow?.defaultBotId ?? "",
    defaultGitTargetBranch: getEnv("DEV_WF_DEFAULT_GIT_TARGET_BRANCH") ?? yaml.devWorkflow?.defaultGitTargetBranch ?? "master",
    workflowTimeoutHours: envInt("DEV_WF_WORKFLOW_TIMEOUT_HOURS", yaml.devWorkflow?.workflowTimeoutHours ?? 72),
    phaseTimeoutBufferMinutes: envInt("DEV_WF_PHASE_TIMEOUT_BUFFER_MINUTES", yaml.devWorkflow?.phaseTimeoutBufferMinutes ?? 5),
    yuqueBookId: yuqueBookIdRaw != null ? Number(yuqueBookIdRaw) : null,
    yuqueToken: getEnv("DEV_WF_YUQUE_TOKEN") ?? yaml.devWorkflow?.yuqueToken ?? "",
    yuqueApiBaseUrl: getEnv("YUQUE_API_BASE_URL") ?? yaml.devWorkflow?.yuqueApiBaseUrl ?? "https://yuque-api.antfin-inc.com",
    yuqueKnowledgeBases: (yaml.devWorkflow?.yuqueKnowledgeBases ?? []).map((kb: { bookId: number; name: string; token: string }) => ({
      bookId: Number(kb.bookId),
      name: String(kb.name),
      token: String(kb.token),
    })),
    gitPlatform: getEnv("DEV_WF_GIT_PLATFORM") ?? yaml.devWorkflow?.gitPlatform ?? "aone",
    gitApiBaseUrl: getEnv("DEV_WF_GIT_API_BASE_URL") ?? yaml.devWorkflow?.gitApiBaseUrl ?? "",
    gitApiToken: getEnv("DEV_WF_GIT_API_TOKEN") ?? yaml.devWorkflow?.gitApiToken ?? "",
    baasEndpoint: getEnv("DEV_WF_BAAS_ENDPOINT") ?? yaml.devWorkflow?.baasEndpoint ?? "",
    baasApiKey: getEnv("DEV_WF_BAAS_API_KEY") ?? yaml.devWorkflow?.baasApiKey ?? "",
  };
}

// ── Log Analysis Config ──

export type LogSource = {
  name: string;
  region: string;
  app: string;
  tenant: string;
  /** Override baseUrl for this source (e.g. antlogs.alipay.com for BCN/secbaas) */
  baseUrl?: string;
  defaultLogstore?: string;
  defaultQuery?: string;
  defaultEnabled?: boolean;
};

export type AntLogsResolvedConfig = {
  apiId: string;
  apiKey: string;
  baseUrl: string;
  requestTimeoutMs: number;
  maxBatonRounds: number;
  sources: LogSource[];
};

export type LogAnalysisConfig = {
  enabled: boolean;
  cron: string;
  logSource: string;
  lookbackMinutes: number;
  minErrorCount: number;
  cooldownMinutes: number;
  analysisApiUrl: string;
  analysisApiKey: string;
  analysisModel: string;
  // ClawFix pipeline settings
  ocbModuleOwners: Record<string, string>;
  dimaWorkspaceId: string;
  dimaProjectId: string;
  /** BaaS Bot ID for log collection via BaaS API (replaces MCP) */
  baasLogBotId: string;
  /** AntLogs Direct API config — null when not configured */
  antlogs: AntLogsResolvedConfig | null;
};

export function resolveLogAnalysisConfig(configPath?: string): LogAnalysisConfig {
  const yaml = readYamlConfig(configPath);
  return {
    enabled: getEnv("LOG_ANALYSIS_ENABLED") !== "false" && (yaml.logAnalysis?.enabled !== false),
    cron: getEnv("LOG_ANALYSIS_CRON") ?? yaml.logAnalysis?.cron ?? "*/10 * * * *",
    logSource: getEnv("LOG_ANALYSIS_LOG_SOURCE") ?? yaml.logAnalysis?.logSource ?? "",
    lookbackMinutes: envInt("LOG_ANALYSIS_LOOKBACK_MINUTES", yaml.logAnalysis?.lookbackMinutes ?? 30),
    minErrorCount: envInt("LOG_ANALYSIS_MIN_ERROR_COUNT", yaml.logAnalysis?.minErrorCount ?? 5),
    cooldownMinutes: envInt("LOG_ANALYSIS_COOLDOWN_MINUTES", yaml.logAnalysis?.cooldownMinutes ?? 60),
    analysisApiUrl: getEnv("LOG_ANALYSIS_API_URL") ?? yaml.logAnalysis?.analysisApiUrl ?? "",
    analysisApiKey: getEnv("LOG_ANALYSIS_API_KEY") ?? yaml.logAnalysis?.analysisApiKey ?? "",
    analysisModel: getEnv("LOG_ANALYSIS_MODEL") ?? yaml.logAnalysis?.analysisModel ?? "",
    // ClawFix pipeline settings
    ocbModuleOwners: (yaml.logAnalysis?.ocbModuleOwners as Record<string, string>) ?? {
      frontend: "153364",
      backend: "205357",
      adapter: "272471",
      openclaw: "272471",
      bcn: "410025",
    },
    dimaWorkspaceId: getEnv("DIMA_WORKSPACE_ID") ?? yaml.logAnalysis?.dimaWorkspaceId ?? "W26001124452",
    dimaProjectId: getEnv("DIMA_PROJECT_ID") ?? yaml.logAnalysis?.dimaProjectId ?? "P26001028964",
    baasLogBotId: getEnv("CLAWFIX_BAAS_LOG_BOT_ID") ?? yaml.logAnalysis?.baasLogBotId ?? "",
    antlogs: resolveAntLogsConfig(yaml.logAnalysis?.antlogs),
  };
}

function resolveAntLogsConfig(yaml?: YamlAntLogsConfig): AntLogsResolvedConfig | null {
  const apiId = getEnv("ANTLOGS_API_ID") ?? yaml?.apiId;
  const apiKey = getEnv("ANTLOGS_API_KEY") ?? yaml?.apiKey;
  if (!apiId || !apiKey) return null;

  const sources: LogSource[] = (yaml?.sources ?? []).map((s) => ({
    name: s.name,
    region: s.region,
    app: s.app,
    tenant: s.tenant,
    baseUrl: s.baseUrl,
    defaultLogstore: s.defaultLogstore,
    defaultQuery: s.defaultQuery,
    defaultEnabled: s.defaultEnabled,
  }));

  return {
    apiId,
    apiKey,
    baseUrl: getEnv("ANTLOGS_BASE_URL") ?? yaml?.baseUrl ?? "https://logs.alipay.com",
    requestTimeoutMs: envInt("ANTLOGS_REQUEST_TIMEOUT_MS", yaml?.requestTimeoutMs ?? 30000),
    maxBatonRounds: envInt("ANTLOGS_MAX_BATON_ROUNDS", yaml?.maxBatonRounds ?? 5),
    sources,
  };
}

function initSqlite(dbPath: string): IDatabase {
  const dir = join(dbPath, "..");
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }

  const raw = new Database(dbPath);
  raw.pragma("journal_mode = WAL");
  raw.pragma("foreign_keys = ON");
  raw.pragma("busy_timeout = 5000");

  console.log(`[clawweb] SQLite opened: ${dbPath}`);
  return new SqliteDatabase(raw);
}

async function initMysql(cfg: ResolvedDbConfig["mysql"], dbType: "mysql" | "zdas" = "mysql"): Promise<IDatabase> {
  const isZdas = dbType === "zdas";
  const pool = mysql.createPool({
    host: cfg.host,
    port: cfg.port,
    user: cfg.user,
    password: cfg.password,
    database: cfg.database,
    waitForConnections: true,
    connectionLimit: cfg.poolSize,
    charset: "utf8mb4",
    // ZDAS: keep session timeout under control to avoid long-running query issues.
    ...(isZdas
      ? {
          connectTimeout: 10000,
          enableKeepAlive: true,
        }
      : {}),
    // NOTE: We use pool.query() (text protocol) instead of pool.execute() (binary
    // protocol) for all write operations. This eliminates server-side Prepared
    // Statement cursor creation, which caused "maximum open cursors exceeded"
    // on OceanBase under high concurrency. Prepared statement caching options
    // are therefore unnecessary — .query() never creates server-side cursors.
  });

  // Verify connection
  const conn = await pool.getConnection();
  try {
    if (isZdas) {
      await conn.query("SET SESSION ob_query_timeout = 30000000"); // 30s
    }
  } finally {
    conn.release();
  }

  console.log(`[clawweb] ${dbType === "zdas" ? "ZDAS" : "MySQL"} connected: ${cfg.host}:${cfg.port}/${cfg.database}`);

  return new MysqlDatabase(pool, dbType);
}

export function getRepositories(): Repositories {
  if (!repos) {
    throw new Error("Database not initialized — call initDatabase() first");
  }
  return repos;
}

export async function closeDatabase(): Promise<void> {
  if (repos) {
    await repos.db.close();
    repos = null;
  }
}

export { SqliteDatabase, runMigrations };
