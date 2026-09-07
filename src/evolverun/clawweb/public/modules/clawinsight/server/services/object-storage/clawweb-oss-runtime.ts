import { MistCredentialProvider } from "./mist-credential-provider.js";
import { MistOssObjectStore } from "./oss-object-store.js";

export type ClawWebEnvironment = "pre" | "prod";

export const DEFAULT_CLAWWEB_OSS_BUCKET = "antsys-agentclaw-prod";
export const DEFAULT_CLAWWEB_OSS_ENDPOINT = "cn-shanghai-ant-internal.oss-alipay.aliyuncs.com";
export const DEFAULT_CLAWWEB_EVOLVE_ARCA_OSS_ENDPOINT = "https://cn-shanghai-ant-office.oss-alipay.aliyuncs.com";
export const DEFAULT_CLAWWEB_SESSION_ANALYSIS_AIS_OSS_ENDPOINT = "cn-shanghai-ant-internal.oss-alipay.aliyuncs.com";
export const DEFAULT_CLAWWEB_OSS_SECRET_NAMES: Record<ClawWebEnvironment, string> = {
  pre: "other_manual_clawweb_agentclaw_oss_pre",
  prod: "other_manual_clawweb_agentclaw_oss",
};

function positiveInteger(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export function normalizeClawWebOssEnvironment(value: string | undefined): ClawWebEnvironment {
  const normalized = value?.trim().toLowerCase();
  return normalized === "prod" || normalized === "gray" ? "prod" : "pre";
}

export function resolveClawWebOssEnvironment(
  env: Record<string, string | undefined> = process.env,
): ClawWebEnvironment {
  return normalizeClawWebOssEnvironment(
    env.CLAWWEB_OSS_ENV
      ?? env.INSIGHT_ENV
      ?? env.SERVER_ENV
      ?? env.REAL_SERVER_ENV
      ?? env.ALIPAY_APP_ENV,
  );
}

export function resolveClawWebMistRuntimeScope(
  environment: ClawWebEnvironment,
  env: Record<string, string | undefined> = process.env,
): { appName: string; mode: string } {
  return {
    appName: env.CLAWWEB_MIST_APP_NAME ?? env.INSIGHT_MIST_APP_NAME ?? "clawweb",
    mode: env.CLAWWEB_MIST_MODE ?? env.INSIGHT_MIST_MODE ?? environment,
  };
}

export function resolveClawWebOssMistConfig(
  environment: ClawWebEnvironment,
  env: Record<string, string | undefined> = process.env,
  options: { strictEnvironment?: boolean } = {},
): { secretName: string; mode: string } {
  const strictEnvironment = options.strictEnvironment === true;
  const runtimeScope = resolveClawWebMistRuntimeScope(environment, env);
  return {
    secretName: strictEnvironment
      ? DEFAULT_CLAWWEB_OSS_SECRET_NAMES[environment]
      : env.CLAWWEB_OSS_MIST_SECRET_NAME
        ?? env.INSIGHT_OSS_MIST_SECRET_NAME
        ?? DEFAULT_CLAWWEB_OSS_SECRET_NAMES[environment],
    mode: strictEnvironment
      ? environment
      : runtimeScope.mode,
  };
}

/**
 * Shared ClawWeb OSS runtime. Feature modules provide only the environment and
 * may override configuration through env vars; credentials remain in memory.
 */
export function createClawWebOssObjectStore(
  environment: ClawWebEnvironment,
  env: Record<string, string | undefined> = process.env,
  options: { strictEnvironment?: boolean; endpoint?: string; signedUrlVersion?: "v1" | "v4" } = {},
): MistOssObjectStore {
  const { secretName, mode: mistMode } = resolveClawWebOssMistConfig(environment, env, options);
  const mistScope = resolveClawWebMistRuntimeScope(environment, env);
  const credentialProvider = new MistCredentialProvider({
    endpoint: env.CLAWWEB_MIST_ENDPOINT ?? env.INSIGHT_MIST_ENDPOINT ?? "127.0.0.1:11004",
    tenant: env.CLAWWEB_MIST_TENANT ?? env.INSIGHT_MIST_TENANT ?? "ALIPAY",
    mode: mistMode,
    appName: mistScope.appName,
    secretName,
    timeoutMs: positiveInteger(env.CLAWWEB_MIST_TIMEOUT_MS ?? env.INSIGHT_MIST_TIMEOUT_MS, 5_000),
    credentialTtlMs: positiveInteger(
      env.CLAWWEB_MIST_CREDENTIAL_TTL_MS ?? env.INSIGHT_MIST_CREDENTIAL_TTL_MS,
      5 * 60_000,
    ),
  });
  return new MistOssObjectStore({
    endpoint: options.endpoint ?? env.CLAWWEB_OSS_ENDPOINT ?? env.INSIGHT_OSS_ENDPOINT ?? DEFAULT_CLAWWEB_OSS_ENDPOINT,
    region: env.CLAWWEB_OSS_REGION ?? "oss-cn-shanghai",
    bucketName: env.CLAWWEB_OSS_BUCKET ?? env.INSIGHT_OSS_BUCKET ?? DEFAULT_CLAWWEB_OSS_BUCKET,
    credentialProvider,
    timeoutMs: positiveInteger(env.CLAWWEB_OSS_TIMEOUT_MS ?? env.INSIGHT_OSS_TIMEOUT_MS, 10_000),
    maxPayloadBytes: positiveInteger(
      env.CLAWWEB_OSS_MAX_OBJECT_BYTES ?? env.INSIGHT_EVIDENCE_MAX_BYTES,
      10 * 1024 * 1024,
    ),
    signedUrlVersion: options.signedUrlVersion,
  });
}
