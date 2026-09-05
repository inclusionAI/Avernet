import type { ResolvedBaasConfig, ResolvedBaasEnvironmentConfig } from "../db.js";

export type BaasCommandEnvironment = "pre" | "prod";

export type BaasCommandTargetConfig = ResolvedBaasEnvironmentConfig
  & Pick<ResolvedBaasConfig, "iamtoken">
  & { environment: BaasCommandEnvironment };

export type BaasCommandResponse = {
  code?: number | string;
  message?: string;
  trace_id?: string;
  buserviceErrorMsg?: string;
  buserviceErrorCode?: string;
  error?: string | { message?: string; code?: string };
  data?: {
    run_id?: string;
    session_id?: string;
    message_id?: string;
    exit_code?: number;
    stdout?: string;
    stderr?: string;
    execution_time_ms?: number;
    result?: { stdout?: string; stderr?: string; exit_code?: number };
  };
};

export function normalizeBaasCommandEnvironment(
  value: string | null | undefined,
): BaasCommandEnvironment {
  const normalized = value?.trim().toLowerCase();
  if (normalized === "pre" || normalized === "prepub") return "pre";
  if (normalized === "prod" || normalized === "gray") return "prod";
  throw new Error(`目标 Bot 运行环境缺失或不支持: ${value ?? "unknown"}`);
}

export function resolveBaasCommandTarget(
  config: ResolvedBaasConfig,
  runtimeEnvironment: string | null | undefined,
): BaasCommandTargetConfig {
  const environment = normalizeBaasCommandEnvironment(runtimeEnvironment);
  const target = config.environments?.[environment];
  if (!target?.baseUrl) throw new Error(`Bot 消息平台未配置 ${environment} baseUrl`);
  if (!target.apiKey) throw new Error(`Bot 消息平台未配置 ${environment} apiKey`);
  return {
    apiKey: target.apiKey,
    iamtoken: config.iamtoken,
    baseUrl: target.baseUrl.replace(/\/$/, ""),
    environment,
  };
}

export function baasRequestHeaders(config: Pick<BaasCommandTargetConfig, "apiKey" | "iamtoken">): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (config.apiKey) headers.Authorization = `Bearer ${config.apiKey}`;
  if (process.platform === "darwin" && config.iamtoken) headers.Cookie = `IAM_TOKEN=${config.iamtoken}`;
  return headers;
}

/** Existing BaaS execute-command transport shared by Evolve and Repair. */
export async function executeBaasCommand<TBody extends BaasCommandResponse = BaasCommandResponse>(input: {
  config: BaasCommandTargetConfig;
  tenant: string;
  deviceId: string;
  cmd: string;
  timeoutSeconds: number;
  env?: Record<string, string>;
  deviceAffinity?: string;
  signal?: AbortSignal;
}): Promise<{ response: Response; body: TBody }> {
  const endpoint = new URL(
    `/api/v1/bots/${encodeURIComponent(input.tenant)}/${encodeURIComponent(input.deviceId)}/execute-command`,
    input.config.baseUrl,
  );
  if (input.deviceAffinity) endpoint.searchParams.set("device_affinity", input.deviceAffinity);
  const response = await fetch(endpoint, {
    method: "POST",
    headers: baasRequestHeaders(input.config),
    body: JSON.stringify({
      cmd: input.cmd,
      ...(input.env ? { env: input.env } : {}),
      timeout_seconds: input.timeoutSeconds,
    }),
    signal: input.signal,
  });
  const body = await response.json().catch(() => ({})) as TBody;
  return { response, body };
}
