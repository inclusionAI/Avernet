/**
 * Environment utilities — single source of truth for environment detection.
 *
 * Reads SERVER_ENV / ALIPAY_APP_ENV (standard Ant Group env vars).
 * Falls back to DATABASE_MODE for local detection.
 *
 * Env values:
 *   - "dev"   — local development, stable, pre-release, or unset
 *   - "pre"   — pre-production / prepub
 *   - "prod"  — production or gray
 */
import type { Stats } from "node:fs";
import { mkdtemp, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const temporaryDirectoryScopePattern = /^[a-z0-9][a-z0-9-]{0,63}$/;
const temporaryDirectoryUnavailableMessage = "临时目录不可用或配置无效";

/** A server-safe error whose public message does not disclose paths, environment values, or raw OS errors. */
export class TemporaryDirectoryError extends Error {
  constructor(cause: unknown) {
    super(temporaryDirectoryUnavailableMessage, { cause });
    this.name = "TemporaryDirectoryError";
  }
}

function configuredTemporaryDirectoryParent(): {
  parent: string;
  configuredValue: string | null;
  source: "CLAWWEB_TEMP_DIR" | "TMPDIR" | "OS_TMPDIR";
} {
  if (process.env.CLAWWEB_TEMP_DIR) {
    return {
      parent: resolve(process.env.CLAWWEB_TEMP_DIR),
      configuredValue: process.env.CLAWWEB_TEMP_DIR,
      source: "CLAWWEB_TEMP_DIR",
    };
  }
  if (process.env.TMPDIR) {
    return {
      parent: resolve(process.env.TMPDIR),
      configuredValue: process.env.TMPDIR,
      source: "TMPDIR",
    };
  }
  return { parent: resolve(tmpdir()), configuredValue: null, source: "OS_TMPDIR" };
}

/** Create a request-local directory under the configured shared temporary volume. */
export async function createConfiguredTemporaryDirectory(scope: string): Promise<string> {
  if (!temporaryDirectoryScopePattern.test(scope)) throw new Error(`Invalid temporary directory scope: ${scope}`);
  const { parent, configuredValue, source } = configuredTemporaryDirectoryParent();
  const prefix = join(parent, `${scope}-`);
  let parentStat: Stats;
  try {
    parentStat = await stat(parent);
  } catch (error) {
    throw new TemporaryDirectoryError({
      configuredValue,
      operation: "stat-parent",
      parent,
      rawError: error,
      scope,
      source,
    });
  }
  if (!parentStat.isDirectory()) {
    throw new TemporaryDirectoryError({
      configuredValue,
      operation: "stat-parent",
      parent,
      rawError: new Error("Configured temporary-directory parent is not a directory"),
      scope,
      source,
    });
  }
  try {
    return await mkdtemp(prefix);
  } catch (error) {
    throw new TemporaryDirectoryError({
      configuredValue,
      operation: "mkdtemp",
      parent,
      prefix,
      rawError: error,
      scope,
      source,
    });
  }
}


const ENV_PRIORITY = ["SERVER_ENV", "REAL_SERVER_ENV", "ALIPAY_APP_ENV"] as const;
const DEFAULT_TRUSTED_REMOTE_ORIGINS = new Set([
  "https://clawweb-pre.alipay.com",
  "https://clawweb.alipay.com",
]);
let configuredPublicBaseUrl: string | undefined;

function getRawEnv(): string {
  for (const key of ENV_PRIORITY) {
    const v = process.env[key];
    if (v) return v.toLowerCase();
  }
  return "";
}

/** Returns "dev" | "pre" | "prod" */
export function getCurrentEnv(): "dev" | "pre" | "prod" {
  const raw = getRawEnv();
  if (raw === "prod" || raw === "gray") return "prod";
  if (raw === "pre" || raw === "prepub") return "pre";
  return "dev";
}

/** Returns "dev" | "pre" | "gray" | "prod" */
export function getCurrentEnvWithGray(): "dev" | "pre" | "gray" | "prod" {
  const raw = getRawEnv();
  if (raw === "prod") return "prod";
  if (raw === "gray") return "gray";
  if (raw === "pre" || raw === "prepub") return "pre";
  return "dev";
}

/** True when running in local dev mode (no Ant Group env vars + sqlite) */
export function isDev(): boolean {
  return getCurrentEnv() === "dev";
}

/** Public origin embedded in task commands that call back into this ClawWeb. */
export function normalizeClawWebPublicBaseUrl(
  value: string,
  trustedRemoteOrigins: ReadonlySet<string> = DEFAULT_TRUSTED_REMOTE_ORIGINS,
): string {
  const raw = value.trim();
  if (!raw || /\s/.test(raw)) throw new Error("ClawWeb public URL must be a non-empty origin without whitespace");
  let parsed: URL;
  try { parsed = new URL(raw); }
  catch { throw new Error("ClawWeb public URL is invalid"); }
  if (parsed.username || parsed.password) throw new Error("ClawWeb public URL must not contain credentials");
  if (parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error("ClawWeb public URL must be an origin without path, query, or fragment");
  }
  const local = parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
  const isDev = getCurrentEnv() === "dev";
  if (local) {
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      throw new Error("Local ClawWeb public URL must use HTTP or HTTPS");
    }
  } else if (isDev && parsed.protocol === "http:") {
    // Dev mode allows HTTP origins for internal network callbacks (e.g. bot inside corp VPN).
  } else if (
    parsed.protocol !== "https:"
    || parsed.port
    || !trustedRemoteOrigins.has(parsed.origin)
  ) {
    throw new Error("ClawWeb public URL is not a trusted HTTPS origin");
  }
  return parsed.origin;
}

/** Configure one exact callback origin explicitly trusted by the embedding Host. */
export function configureClawWebPublicBaseUrl(
  value: string | undefined,
  trustedRemoteOrigins: readonly string[] = [],
): void {
  if (!value) {
    configuredPublicBaseUrl = undefined;
    return;
  }
  // COSEC: Remote callback origins must pass URL parsing and an exact origin allowlist.
  const allowed = new Set([...DEFAULT_TRUSTED_REMOTE_ORIGINS, ...trustedRemoteOrigins]);
  configuredPublicBaseUrl = normalizeClawWebPublicBaseUrl(value, allowed);
}

export function getClawWebPublicBaseUrl(): string {
  if (configuredPublicBaseUrl) return configuredPublicBaseUrl;
  const override = process.env.CLAWWEB_PUBLIC_BASE_URL?.trim()
    || process.env.CLAWWEB_URL?.trim();
  if (override) return normalizeClawWebPublicBaseUrl(override);
  const environment = getCurrentEnv();
  if (environment === "pre") return normalizeClawWebPublicBaseUrl("https://clawweb-pre.alipay.com");
  if (environment === "prod") return normalizeClawWebPublicBaseUrl("https://clawweb.alipay.com");
  return normalizeClawWebPublicBaseUrl("http://localhost:3001");
}
