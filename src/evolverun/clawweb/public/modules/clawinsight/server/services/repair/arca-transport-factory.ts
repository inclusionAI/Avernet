import type { RepairConfig } from "./config.js";
import { ArcaCommandTransport, DirectArcaConnectionProvider, type ArcaConnectionProvider } from "./arca-command-transport.js";
import { OcbOwnerArcaConnectionProvider } from "./ocb-owner-arca-connection-provider.js";
import { OCB_RESTART_BASE_URLS } from "./ocb-gateway.js";
import { MistSecretValueProvider } from "../object-storage/mist-credential-provider.js";
import { resolveClawWebMistRuntimeScope, resolveClawWebOssEnvironment } from "../object-storage/clawweb-oss-runtime.js";

function positiveInteger(value: string | undefined, fallback: number) {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

/** Composition root: local Owner SSO is explicit; deployed services retain MIST. */
export function createRepairArcaConnectionProvider(
  config: Pick<RepairConfig, "publicBaseUrl" | "requestTimeoutMs">,
  env: Record<string, string | undefined>,
): ArcaConnectionProvider {
  const mode = env.REPAIR_ARCA_CONNECTION_MODE ?? "mist";
  if (mode === "ocb_owner") {
    const url = new URL(config.publicBaseUrl);
    if (!["http:", "https:"].includes(url.protocol) || !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) {
      throw new Error("ocb_owner connection mode is restricted to a local Repair control plane");
    }
    return new OcbOwnerArcaConnectionProvider({
      baseUrls: OCB_RESTART_BASE_URLS,
      timeoutMs: config.requestTimeoutMs,
      getIdentity: () => ({
        cookie: env.REPAIR_ARCA_LOCAL_IAM_TOKEN ? "IAM_TOKEN=" + env.REPAIR_ARCA_LOCAL_IAM_TOKEN : "",
        userId: env.REPAIR_ARCA_LOCAL_OWNER_ID ?? "",
      }),
    });
  }
  if (mode !== "mist") throw new Error("Unknown Repair ARCA connection mode");
  const scope = resolveClawWebMistRuntimeScope(resolveClawWebOssEnvironment(env), env);
  return new DirectArcaConnectionProvider(new MistSecretValueProvider({
    endpoint: env.CLAWWEB_MIST_ENDPOINT ?? env.INSIGHT_MIST_ENDPOINT ?? "127.0.0.1:11004",
    tenant: env.CLAWWEB_MIST_TENANT ?? env.INSIGHT_MIST_TENANT ?? "ALIPAY",
    mode: scope.mode, appName: scope.appName,
    secretName: env.REPAIR_ARCA_PROXYPASS_MIST_SECRET_NAME ?? "other_manual_agentclawproxy_proxypass_secret",
    timeoutMs: positiveInteger(env.CLAWWEB_MIST_TIMEOUT_MS ?? env.INSIGHT_MIST_TIMEOUT_MS, 5_000),
    credentialTtlMs: positiveInteger(env.CLAWWEB_MIST_CREDENTIAL_TTL_MS ?? env.INSIGHT_MIST_CREDENTIAL_TTL_MS, 5 * 60_000),
  }));
}

export function createRepairArcaTransport(config: RepairConfig, env: Record<string, string | undefined>) {
  return new ArcaCommandTransport({
    connectionProvider: createRepairArcaConnectionProvider(config, env),
    proxyBaseUrls: { pre: env.REPAIR_ARCA_PROXY_PRE_BASE_URL, prod: env.REPAIR_ARCA_PROXY_PROD_BASE_URL },
    tokenTtlSeconds: positiveInteger(env.REPAIR_ARCA_PROXYPASS_TOKEN_TTL_SECONDS, 120),
    timeoutSeconds: config.baas.commandTimeoutSeconds,
  });
}
