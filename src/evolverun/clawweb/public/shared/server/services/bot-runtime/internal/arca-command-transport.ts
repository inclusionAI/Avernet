import { createHmac } from "node:crypto";
import type { ArcaConnectionProvider } from "../contracts.js";
type BotEnvironment = "pre" | "prod";
export type { ArcaConnectionProvider } from "../contracts.js";
import { BotRuntimeError as BotConnectionError, runtimeValidation as connectionValidation } from "../errors.js";
import { redactText } from "../../redaction.js";

export const ARCA_TERMINAL_PORT = 20_003;

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
  proxyBaseUrls?: Partial<Record<BotEnvironment, string>>;
  tokenTtlSeconds?: number;
  timeoutSeconds?: number;
};

export function normalizeArcaSandboxId(value: unknown): string {
  const normalized = typeof value === "string" || typeof value === "number"
    ? String(value).trim().split("@", 1)[0]
    : "";
  if (!normalized || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$/u.test(normalized)) {
    connectionValidation("invalid_arca_sandbox_id", "ARCA sandbox_id 格式不合法");
  }
  return normalized;
}

function normalizeArcaInstanceId(value: unknown): string {
  const normalized = typeof value === "string" || typeof value === "number"
    ? String(value).trim()
    : "";
  if (!normalized
    || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,255}(?:@[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$/u.test(normalized)) {
    connectionValidation("invalid_arca_instance_id", "ARCA instance id 格式不合法");
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
    environment: BotEnvironment;
    bindingId: string;
    sandboxId: string;
    arcaInstanceId?: string;
    ttlSeconds: number;
    port?: number;
  }): Promise<{ target: string; token: string }> {
    if (!Number.isSafeInteger(input.ttlSeconds) || input.ttlSeconds < 30 || input.ttlSeconds > 600) {
      connectionValidation("invalid_arca_connection_ttl", "ARCA 连接凭据 TTL 必须在 30 到 600 秒之间");
    }
    const port = input.port ?? ARCA_TERMINAL_PORT;
    if (!Number.isSafeInteger(port) || port < 1 || port > 65535) connectionValidation("invalid_arca_port", "Invalid container port");
    const instanceId = normalizeArcaInstanceId(input.arcaInstanceId ?? input.sandboxId);
    if (normalizeArcaSandboxId(instanceId) !== normalizeArcaSandboxId(input.sandboxId)) {
      throw new BotConnectionError(409, "repair_arca_target_mismatch", "ARCA instance id 与冻结目标不匹配");
    }
    const target = `ARCA_${instanceId}:${port}`;
    const header = base64UrlJson({ alg: "HS256", typ: "JWT" });
    const payload = base64UrlJson({ target, exp: this.nowSeconds() + input.ttlSeconds });
    const secret = await this.secretProvider.getSecretValue();
    const signature = createHmac("sha256", secret).update(`${header}.${payload}`).digest("base64url");
    const token = [header, payload, signature].join(".");
    return { target, token };
  }
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
  port = ARCA_TERMINAL_PORT,
): { target: string; token: string } {
  const target = typeof connection.target === "string" ? connection.target.trim() : "";
  const token = typeof connection.token === "string" ? connection.token.trim() : "";
  const match = /^ARCA_([A-Za-z0-9][A-Za-z0-9._-]{0,255}(?:@[A-Za-z0-9][A-Za-z0-9._-]{0,63})?):([0-9]{1,5})$/u.exec(target);
  if (!match || Number(match[2]) !== port || normalizeArcaSandboxId(match[1]) !== normalizeArcaSandboxId(sandboxId)) {
    throw new BotConnectionError(502, "repair_arca_connection_invalid", "ARCA 代理连接目标与冻结目标不匹配");
  }
  if (token.length > 8_192 || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/u.test(token)) {
    throw new BotConnectionError(502, "repair_arca_connection_invalid", "ARCA 代理连接凭据不合法");
  }
  return { target, token };
}

export class ArcaCommandTransport {
  private readonly baseUrls: Partial<Record<BotEnvironment, URL>>;
  private readonly tokenTtlSeconds: number;
  private readonly timeoutSeconds: number;

  constructor(private readonly options: ArcaCommandTransportOptions) {
    this.baseUrls = {
      pre: options.proxyBaseUrls?.pre ? validatedBaseUrl(options.proxyBaseUrls.pre) : undefined,
      prod: options.proxyBaseUrls?.prod ? validatedBaseUrl(options.proxyBaseUrls.prod) : undefined,
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
    environment: BotEnvironment;
    bindingId: string;
    sandboxId: string;
    arcaInstanceId?: string;
    command: string;
    timeoutMs?: number;
  }): Promise<ArcaCommandResult> {
    if (!this.baseUrls[input.environment]) throw new BotConnectionError(503, "arca_not_configured", "ARCA proxy origin is not configured");
    const sandboxId = normalizeArcaSandboxId(input.sandboxId);
    const resolved = await this.options.connectionProvider.getConnection({
      environment: input.environment,
      bindingId: input.bindingId,
      sandboxId,
      arcaInstanceId: input.arcaInstanceId,
      ttlSeconds: this.tokenTtlSeconds,
    });
    const connection = validateArcaProxyConnection(resolved, sandboxId);
    // Only the explicit local Owner provider supplies this transient identity.
    // MIST connections continue to send just their target-scoped token.
    const localOwner = resolved.localOwnerIdentity;
    const localHeaders: Record<string, string> = {};
    if (localOwner) {
      if (!localOwner.cookie?.trim() || !localOwner.userId?.trim()
        || /[\r\n\0]/u.test(localOwner.cookie + localOwner.userId)) {
        throw new BotConnectionError(401, "repair_ocb_identity_required", "本地 ARCA 请求缺少有效 Owner 登录身份");
      }
      localHeaders.Cookie = localOwner.cookie;
      localHeaders["x-user-id"] = localOwner.userId;
    }
    const endpoint = new URL(
      `/proxypass/${connection.target}/arca/api/v1/sandbox/${encodeURIComponent(sandboxId)}/terminal/exec_command`,
      this.baseUrls[input.environment],
    );
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), input.timeoutMs ?? (this.timeoutSeconds + 5) * 1_000);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        redirect: "error",
        headers: {
          ...localHeaders,
          Accept: "application/json",
          "Content-Type": "application/json",
          "x-proxypass-token": connection.token,
          "x-agent-sandbox-id": sandboxId,
        },
        body: JSON.stringify({ command: input.command }),
        signal: controller.signal,
      });
      if (localOwner && response.status >= 300 && response.status < 400) {
        throw new BotConnectionError(401, "repair_ocb_identity_rejected", "本地 ARCA 登录身份失效，请刷新登录后重试");
      }
      const body = await response.json().catch(() => ({})) as ArcaTerminalResponse;
      if (body.buserviceErrorCode === "USER_NOT_LOGIN") {
        throw new BotConnectionError(502, "repair_arca_proxy_rejected", "ARCA 代理拒绝了短期连接凭据");
      }
      if (response.status === 401 || response.status === 403) {
        throw new BotConnectionError(502, "repair_arca_proxy_rejected", "ARCA 代理拒绝了短期连接凭据");
      }
      const data = body.data ?? {};
      const exitCode = finiteNumber(data.exit_code);
      const stdout = outputText(data.outputs, "stdout");
      const responseError = typeof body.buserviceErrorMsg === "string"
        ? body.buserviceErrorMsg
        : typeof body.message === "string" ? body.message : "";
      const stderr = outputText(data.outputs, "stderr") || (!response.ok || body.success === false ? responseError : "");
      // Arca uses opaque service codes (e.g. 13-200000); success/status/exit_code define completion.
      const succeeded = response.ok && body.success !== false && body.data != null
        && (body.buserviceErrorCode == null || [0, "0", ""].includes(body.buserviceErrorCode as string | number))
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
        throw new BotConnectionError(504, "repair_arca_timeout", "ARCA 操作超时；远端执行状态未知");
      }
      if (error instanceof BotConnectionError) throw error;
      throw new BotConnectionError(
        502,
        "repair_arca_failed",
        `ARCA 操作失败: ${redactText(error instanceof Error ? error.message : String(error), 2_000)}`,
      );
    } finally {
      clearTimeout(timeout);
    }
  }
}
