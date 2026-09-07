import { createHmac } from "node:crypto";
import type { RepairTargetEnvironment } from "./contracts.js";
import { RepairError, repairValidation } from "./errors.js";
import { redactPersistableLines, redactText } from "./redaction.js";

const ARCA_PROXY_BASE_URLS: Record<RepairTargetEnvironment, string> = {
  pre: "https://agentclawproxy-pre.alipay.com",
  prod: "https://agentclawproxy-prod.alipay.com",
};
export const ARCA_TERMINAL_PORT = 20_003;

export interface ArcaConnectionProvider {
  getConnection(input: {
    environment: RepairTargetEnvironment;
    bindingId: string;
    sandboxId: string;
    arcaInstanceId?: string;
    ttlSeconds: number;
    authHeaders: Record<string, string>;
  }): Promise<{ target: string; token: string }>;
}

export interface SecretValueProvider {
  getSecretValue(): Promise<string>;
}

type ArcaTerminalOutput = {
  output_type?: unknown;
  text?: unknown;
};

type ArcaTerminalResponse = {
  success?: unknown;
  code?: unknown;
  message?: unknown;
  buserviceErrorCode?: unknown;
  buserviceErrorMsg?: unknown;
  data?: {
    status?: unknown;
    outputs?: unknown;
    exit_code?: unknown;
    execution_time_ms?: unknown;
    duration_ms?: unknown;
  } | null;
};

export type ArcaCommandResult = {
  status: "success" | "failed";
  exitCode: number | null;
  stdout: string;
  stderr: string;
  durationMs: number | null;
};

export type ArcaCommandTransportOptions = {
  connectionProvider: ArcaConnectionProvider;
  proxyBaseUrls?: Partial<Record<RepairTargetEnvironment, string>>;
  tokenTtlSeconds?: number;
  timeoutSeconds?: number;
};

export function normalizeArcaSandboxId(value: unknown): string {
  const normalized = typeof value === "string" || typeof value === "number"
    ? String(value).trim().split("@", 1)[0]
    : "";
  if (!normalized || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$/u.test(normalized)) {
    repairValidation("invalid_arca_sandbox_id", "ARCA sandbox_id 格式不合法");
  }
  return normalized;
}

function normalizeArcaInstanceId(value: unknown): string {
  const normalized = typeof value === "string" || typeof value === "number"
    ? String(value).trim()
    : "";
  if (!normalized
    || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,255}(?:@[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$/u.test(normalized)) {
    repairValidation("invalid_arca_instance_id", "ARCA instance id 格式不合法");
  }
  return normalized;
}

function base64UrlJson(value: unknown): string {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
}

/** Creates the same scoped HS256 proxypass credential used by OCB's ARCA adapter. */
export class DirectArcaConnectionProvider implements ArcaConnectionProvider {
  constructor(
    private readonly secretProvider: SecretValueProvider,
    private readonly nowSeconds: () => number = () => Math.floor(Date.now() / 1_000),
  ) {}

  async getConnection(input: {
    environment: RepairTargetEnvironment;
    bindingId: string;
    sandboxId: string;
    arcaInstanceId?: string;
    ttlSeconds: number;
    authHeaders: Record<string, string>;
  }): Promise<{ target: string; token: string }> {
    if (!Number.isSafeInteger(input.ttlSeconds) || input.ttlSeconds < 30 || input.ttlSeconds > 600) {
      repairValidation("invalid_arca_connection_ttl", "ARCA 连接凭据 TTL 必须在 30 到 600 秒之间");
    }
    const instanceId = normalizeArcaInstanceId(input.arcaInstanceId ?? input.sandboxId);
    if (normalizeArcaSandboxId(instanceId) !== normalizeArcaSandboxId(input.sandboxId)) {
      throw new RepairError(409, "repair_arca_target_mismatch", "ARCA instance id 与冻结目标不匹配");
    }
    const target = `ARCA_${instanceId}:${ARCA_TERMINAL_PORT}`;
    const header = base64UrlJson({ alg: "HS256", typ: "JWT" });
    const payload = base64UrlJson({ target, exp: this.nowSeconds() + input.ttlSeconds });
    const secret = await this.secretProvider.getSecretValue();
    const signature = createHmac("sha256", secret).update(`${header}.${payload}`).digest("base64url");
    return { target, token: `${header}.${payload}.${signature}` };
  }
}

function requiredRelayHeader(authHeaders: Record<string, string>, name: string): string {
  const found = Object.entries(authHeaders).find(([key]) => key.toLowerCase() === name)?.[1]?.trim();
  if (!found) {
    throw new RepairError(401, "repair_arca_identity_required", "ARCA 运行态访问需要当前 Owner 登录身份");
  }
  return found;
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function outputText(outputs: unknown, kind: "stdout" | "stderr"): string {
  if (!Array.isArray(outputs)) return "";
  return outputs
    .filter((item): item is ArcaTerminalOutput => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    .filter((item) => item.output_type === kind && typeof item.text === "string")
    .map((item) => String(item.text))
    .join("");
}

function validatedBaseUrl(value: string): URL {
  const url = new URL(value);
  if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/") {
    throw new Error("ARCA proxy base URL 必须是无凭据的 HTTPS origin");
  }
  return url;
}

export function validateArcaProxyConnection(
  connection: { target: unknown; token: unknown },
  sandboxId: string,
): { target: string; token: string } {
  const target = typeof connection.target === "string" ? connection.target.trim() : "";
  const token = typeof connection.token === "string" ? connection.token.trim() : "";
  const match = /^ARCA_([A-Za-z0-9][A-Za-z0-9._-]{0,255}(?:@[0-9]{1,10})?):20003$/u.exec(target);
  if (!match || normalizeArcaSandboxId(match[1]) !== normalizeArcaSandboxId(sandboxId)) {
    throw new RepairError(502, "repair_arca_connection_invalid", "ARCA 代理连接目标与冻结目标不匹配");
  }
  if (token.length > 8_192 || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/u.test(token)) {
    throw new RepairError(502, "repair_arca_connection_invalid", "ARCA 代理连接凭据不合法");
  }
  return { target, token };
}

export class ArcaCommandTransport {
  private readonly baseUrls: Record<RepairTargetEnvironment, URL>;
  private readonly tokenTtlSeconds: number;
  private readonly timeoutSeconds: number;

  constructor(private readonly options: ArcaCommandTransportOptions) {
    this.baseUrls = {
      pre: validatedBaseUrl(options.proxyBaseUrls?.pre ?? ARCA_PROXY_BASE_URLS.pre),
      prod: validatedBaseUrl(options.proxyBaseUrls?.prod ?? ARCA_PROXY_BASE_URLS.prod),
    };
    this.tokenTtlSeconds = options.tokenTtlSeconds ?? 120;
    this.timeoutSeconds = options.timeoutSeconds ?? 30;
    if (!Number.isSafeInteger(this.tokenTtlSeconds) || this.tokenTtlSeconds < 30 || this.tokenTtlSeconds > 600) {
      throw new Error("ARCA proxypass token TTL 必须在 30 到 600 秒之间");
    }
    if (!Number.isSafeInteger(this.timeoutSeconds) || this.timeoutSeconds < 1 || this.timeoutSeconds > 600) {
      throw new Error("ARCA command timeout 必须在 1 到 600 秒之间");
    }
  }

  async execute(input: {
    environment: RepairTargetEnvironment;
    bindingId: string;
    sandboxId: string;
    arcaInstanceId?: string;
    command: string;
    authHeaders: Record<string, string>;
  }): Promise<ArcaCommandResult> {
    const sandboxId = normalizeArcaSandboxId(input.sandboxId);
    const cookie = requiredRelayHeader(input.authHeaders, "cookie");
    const userId = requiredRelayHeader(input.authHeaders, "x-user-id");
    const ownerAuthHeaders = { Cookie: cookie, "x-user-id": userId };
    const connection = validateArcaProxyConnection(
      await this.options.connectionProvider.getConnection({
        environment: input.environment,
        bindingId: input.bindingId,
        sandboxId,
        arcaInstanceId: input.arcaInstanceId,
        ttlSeconds: this.tokenTtlSeconds,
        authHeaders: ownerAuthHeaders,
      }),
      sandboxId,
    );
    const endpoint = new URL(
      `/proxypass/${connection.target}/arca/api/v1/sandbox/${encodeURIComponent(sandboxId)}/terminal/exec_command`,
      this.baseUrls[input.environment],
    );
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), (this.timeoutSeconds + 5) * 1_000);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          Cookie: cookie,
          "x-user-id": userId,
          "x-proxypass-token": connection.token,
          "x-agent-sandbox-id": sandboxId,
        },
        body: JSON.stringify({ command: input.command }),
        signal: controller.signal,
      });
      const body = await response.json().catch(() => ({})) as ArcaTerminalResponse;
      if (body.buserviceErrorCode === "USER_NOT_LOGIN") {
        throw new RepairError(401, "repair_arca_identity_required", "ARCA 运行态访问需要当前 Owner 登录身份");
      }
      if (response.status === 401 || response.status === 403) {
        throw new RepairError(502, "repair_arca_proxy_rejected", "ARCA 代理拒绝了短期连接凭据");
      }
      const data = body.data ?? {};
      const exitCode = finiteNumber(data.exit_code);
      const stdout = redactPersistableLines(outputText(data.outputs, "stdout"), 32 * 1_024);
      const responseError = typeof body.buserviceErrorMsg === "string"
        ? body.buserviceErrorMsg
        : typeof body.message === "string" ? body.message : "";
      const stderr = redactPersistableLines(
        outputText(data.outputs, "stderr") || (!response.ok || body.success === false ? responseError : ""),
        32 * 1_024,
      );
      const succeeded = response.ok && body.success !== false && body.data != null
        && (exitCode == null || exitCode === 0)
        && (typeof data.status !== "string" || data.status.toLowerCase() === "completed");
      return {
        status: succeeded ? "success" : "failed",
        exitCode,
        stdout,
        stderr,
        durationMs: finiteNumber(data.execution_time_ms ?? data.duration_ms),
      };
    } catch (error) {
      if (controller.signal.aborted) {
        throw new RepairError(504, "repair_arca_timeout", "ARCA 操作超时；远端执行状态未知");
      }
      if (error instanceof RepairError) throw error;
      throw new RepairError(
        502,
        "repair_arca_failed",
        `ARCA 操作失败: ${redactText(error instanceof Error ? error.message : String(error), 2_000)}`,
      );
    } finally {
      clearTimeout(timeout);
    }
  }
}
