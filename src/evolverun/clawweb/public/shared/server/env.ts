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

const ENV_PRIORITY = ["SERVER_ENV", "REAL_SERVER_ENV", "ALIPAY_APP_ENV"] as const;

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
export function normalizeClawWebPublicBaseUrl(value: string): string {
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
    || (parsed.hostname !== "clawweb-pre.alipay.com" && parsed.hostname !== "clawweb.alipay.com")
    || parsed.port
  ) {
    throw new Error("ClawWeb public URL is not a trusted HTTPS origin");
  }
  return parsed.origin;
}

export function getClawWebPublicBaseUrl(): string {
  const override = process.env.CLAWWEB_PUBLIC_BASE_URL?.trim()
    || process.env.CLAWWEB_URL?.trim();
  if (override) return normalizeClawWebPublicBaseUrl(override);
  const environment = getCurrentEnv();
  if (environment === "pre") return normalizeClawWebPublicBaseUrl("https://clawweb-pre.alipay.com");
  if (environment === "prod") return normalizeClawWebPublicBaseUrl("https://clawweb.alipay.com");
  return normalizeClawWebPublicBaseUrl("http://localhost:3001");
}
