/**
 * YAML-based configuration loader for the database layer.
 *
 * Reads `configs/application.yaml` (aligned with OCB backend structure)
 * and resolves database connection settings. Environment variables
 * override YAML values when set.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { parse as parseYaml } from "yaml";
import type { DatabaseConfig, MySqlConfig, ApiConfig } from "./types.js";

// ── Types ──

type YamlDatabaseConfig = {
  /** Deprecated alias: use "mode" instead. */
  type?: "sqlite" | "prod" | "api";
  mode?: "sqlite" | "prod" | "api";
  api?: {
    baseUrl?: string;
    privateKeyB64?: string;
    iamtoken?: string;
    timeout?: number;
    maxRetries?: number;
  };
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
};

type YamlApiLike = {
  baseUrl?: string;
  privateKeyB64?: string;
  iamtoken?: string;
  timeout?: number;
  maxRetries?: number;
};

type YamlAppConfig = {
  database?: YamlDatabaseConfig;
  api?: YamlApiLike;
};

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
  () => join(homedir(), "openclawExt", "taskguard"),
  () => process.env.OPENCLAW_EXTENSION_DIR || "",
  () => "/usr/local/openclaw/extensions/taskguard",
];

/**
 * Find the plugin's own config file.
 *
 * Full traversal order:
 * 1. Explicit path (if provided)
 * 2. CLAWMIND_CONFIG_PATH env var (exact file path)
 * 3. OPENCLAW_EXTENSIONS_DIR env var → {dir}/clawmind/configs/
 * 4. Known plugin installation directories
 * 5. Walk up from this file's location to find package.json + configs/
 */
function findConfigFile(explicitPath?: string): string | null {
  if (explicitPath && existsSync(explicitPath)) {
    return explicitPath;
  }

  // 1. Explicit config file path via env var
  const envPath = getEnv("CLAWMIND_CONFIG_PATH");
  if (envPath && existsSync(envPath)) {
    return envPath;
  }

  // Helper: try a directory for the config filename
  function tryConfigDir(dir: string): string | null {
    const candidate = join(dir, "configs", CONFIG_FILENAME);
    if (existsSync(candidate)) return candidate;
    return null;
  }

  // 2. OpenClaw extensions dir via env var
  const extDir = getEnv("OPENCLAW_EXTENSIONS_DIR");
  if (extDir) {
    const found = tryConfigDir(extDir);
    if (found) return found;
  }

  // 3. Known installation directories
  for (const dirFn of KNOWN_EXTENSION_DIRS) {
    const dir = dirFn();
    if (!dir) continue;
    const found = tryConfigDir(dir);
    if (found) return found;
  }

  // 4. Fallback: walk up from this file's location to find package.json + configs/
  let thisDir: string;
  try {
    thisDir = import.meta.dirname;
  } catch {
    thisDir = new URL(".", import.meta.url).pathname;
  }
  for (let dir = thisDir, i = 0; i < 20; i++) {
    if (existsSync(join(dir, "package.json"))) {
      const found = tryConfigDir(dir);
      if (found) return found;
      break;
    }
    const parent = join(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }

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

function readOpenClawPluginConfig(options: { useDefaultPath: boolean }): Partial<YamlAppConfig> {
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
    if (!clawmind || !isPlainObject(clawmind.config)) return {};
    return clawmind.config as Partial<YamlAppConfig>;
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[db] Failed to read OpenClaw plugin config ${configPath}: ${msg}, ignoring`);
    return {};
  }
}

function envInt(key: string, fallback: number): number {
  const raw = getEnv(key);
  if (!raw) return fallback;
  const parsed = parseInt(raw, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

// ── Load & resolve ──

/** Load database configuration from YAML file, with env var overrides. */
export function loadDatabaseConfig(configPath?: string): DatabaseConfig {
  const filePath = findConfigFile(configPath);

  let yamlConfig: YamlAppConfig = {};
  if (filePath) {
    try {
      const raw = readFileSync(filePath, "utf-8");
      yamlConfig = parseYaml(raw) as YamlAppConfig;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] Failed to read config file ${filePath}: ${msg}, using defaults`);
    }
  }
  yamlConfig = deepMerge<YamlAppConfig>(
    yamlConfig,
    readOpenClawPluginConfig({ useDefaultPath: !configPath }),
  ) ?? {};

  const dbSection = yamlConfig.database ?? {};
  const zdasSection = dbSection.zdas ?? {};
  const datasource = zdasSection.datasources?.[0];

  // Read API config from top-level `api:` section (shared with app config).
  // Falls back to `database.api:` for backward compatibility only.
  const topApi = yamlConfig.api;
  const dbApi = dbSection.api as YamlApiLike | undefined;
  const apiSource: YamlApiLike = topApi ?? dbApi ?? {};

  // Resolve mode: env var > yaml (mode > type alias) > default "sqlite"
  const envMode = getEnv("DATABASE_MODE") as "sqlite" | "prod" | "api" | undefined;
  const mode: "sqlite" | "prod" | "api" = envMode ?? dbSection.mode ?? dbSection.type ?? "sqlite";

  // Resolve sqlite path
  const sqlitePathRaw =
    getEnv("SQLITE_PATH") ?? dbSection.sqlite?.path ?? join(homedir(), ".openclaw", "workflow", "engine.db");
  const sqlitePath = sqlitePathRaw.replace(/^~/, homedir());

  if (mode === "api") {
    const apiConfig: ApiConfig = {
      baseUrl: getEnv("CLAWWEB_API_URL") ?? apiSource.baseUrl ?? "http://localhost:3001",
      privateKeyB64: getEnv("CLAWMIND_PRIVATE_KEY") ?? apiSource.privateKeyB64 ?? "",
      iamtoken: getEnv("CLAWMIND_IAM_TOKEN") ?? apiSource.iamtoken,
      timeout: envInt("CLAWWEB_API_TIMEOUT", apiSource.timeout ?? 5000),
      maxRetries: envInt("CLAWWEB_API_MAX_RETRIES", apiSource.maxRetries ?? 3),
    };
    return { type: "api", sqlitePath, api: apiConfig };
  }

  if (mode === "sqlite") {
    return { type: "sqlite", sqlitePath };
  }

  // Resolve MySQL/ZDAS config from env vars > yaml datasource
  const mysqlConfig: MySqlConfig = {
    host: getEnv("ZDAS_HOST") ?? datasource?.host ?? "127.0.0.1",
    port: envInt("ZDAS_PORT", Number(datasource?.port ?? 11306)),
    user: getEnv("ZDAS_USER") ?? datasource?.user ?? "",
    password: getEnv("ZDAS_PASSWORD") ?? datasource?.password ?? "",
    database: getEnv("ZDAS_DATABASE") ?? datasource?.database ?? "",
    poolSize: envInt("ZDAS_POOL_SIZE", datasource?.pool_size ?? 10),
    poolMin: envInt("ZDAS_POOL_MIN", datasource?.pool_min ?? 5),
  };

  return { type: "mysql", sqlitePath, mysql: mysqlConfig };
}

/** Get the config file path for diagnostics (without reading it). */
export function resolveConfigPath(configPath?: string): string | null {
  return findConfigFile(configPath);
}
