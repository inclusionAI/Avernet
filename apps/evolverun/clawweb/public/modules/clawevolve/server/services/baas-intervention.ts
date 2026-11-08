/**
 * BaasInterventionService — sends intervention messages via BaaS OpenAPI.
 *
 * Uses the message mode (POST /openapi/v1/messages) to deliver intervention
 * commands to the originating bot's session. The bot then executes the
 * command (e.g. /workflow retry --node xxx) in the chat session.
 *
 * iamtoken is only required on macOS (local dev). On server (Linux) the
 * application-level apiKey is sufficient — BaaS authenticates via apiKey alone.
 */
import { resolveBaasConfig } from "@avernet/clawweb-shared/server/db";
import { redactText } from "@avernet/clawweb-shared/server/services/redaction";

/** Whether the current transport target requires the macOS IAM browser cookie. */
function requiresIamToken(baseUrl: string): boolean {
  if (process.platform !== "darwin") return false;
  try {
    const hostname = new URL(baseUrl).hostname.toLowerCase();
    return !["127.0.0.1", "localhost", "::1", "[::1]"].includes(hostname);
  } catch {
    return true;
  }
}

export type InterventionParams = {
  /** BaaS-format bot_id: "real_bot_id:staff_no" (e.g. "default:151614") */
  botId: string;
  /**
   * @deprecated BaaS session identity comes from `sessionId`. A synthesized key
   * is not a real session and was rejected when sent as `metadata.session_id`.
   * Omit both fields to start a new session.
   */
  sessionKey?: string;
  /**
   * Resolved session UUID for BaaS routing (e.g. "74313981-7237-45c3-a949-a899b959afc2").
   * Pass null to start a new session; `session_id` is then omitted from the request.
   */
  sessionId: string | null;
  /** Constructed intervention message with executable command */
  message: string;
  /** Explicit Bot lifecycle target; ClawEvolve control messages use draft. */
  lifecycleStage?: "draft" | "verify" | "online";
  /** Optional environment-specific transport selected by the owning workflow. */
  transportConfig?: { apiKey: string; iamtoken: string; baseUrl: string };
};

export type InterventionResult = {
  ok: boolean;
  messageId?: string;
  sessionId?: string;
  error?: string;
  tokenExpired?: boolean;
};

type BaasMessageResponse = {
  code?: number;
  message?: string;
  data?: {
    message_id?: string;
    session_id?: string;
  };
  trace_id?: string;
  buserviceErrorCode?: string;
  buserviceErrorMsg?: string;
  detail?: unknown;
};

/** BaaS error codes indicating token issues */
const TOKEN_EXPIRED_CODES = ["TOKEN_EXPIRED", "TOKEN_INVALID", "USER_NOT_LOGIN"];

/**
 * BaaS routes on "<bot_id>:<owner_staff_no>" and rejects a bare id before any
 * binding lookup, which surfaces as an unattributable HTTP 400.
 */
const BAAS_BOT_ID_PATTERN = /^[^:\s]+:[^:\s]+$/;

/** Keeps the non-JSON fallback snippet on one readable error line. */
const NON_JSON_SNIPPET_CHARS = 200;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function errorText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function errorCode(value: unknown): string | undefined {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : errorText(value);
}

/**
 * Read only diagnostic fields. Validation input and ctx are never serialized,
 * and JSON bodies are never echoed, so a BaaS error envelope cannot leak the
 * request that produced it.
 */
function errorDetails(response: BaasMessageResponse): string {
  const detail = isRecord(response.detail) ? response.detail : {};
  const code = errorCode(response.buserviceErrorCode) ?? errorCode(detail.code)
    ?? errorCode(detail.error_code) ?? errorCode(response.code);
  let message = errorText(response.buserviceErrorMsg) ?? errorText(response.message)
    ?? errorText(detail.message) ?? errorText(response.detail);
  if (!message && Array.isArray(response.detail)) {
    message = response.detail.map((item: unknown) => {
      if (!isRecord(item)) return "";
      const msg = errorText(item.msg);
      if (!msg) return "";
      const loc = Array.isArray(item.loc) ? item.loc.map(errorCode).filter(Boolean).join(".") : "";
      return loc ? `${loc}: ${msg}` : msg;
    }).filter(Boolean).join("; ");
  }
  const trace = errorText(response.trace_id);
  return [code, message, trace ? `(trace_id=${trace})` : ""].filter(Boolean).join(" ");
}

/**
 * Describe a response whose body yielded no diagnostic field.
 *
 * A non-JSON body means an intermediary (proxy or WAF) rejected the call before
 * BaaS could answer. Reporting the content type and a bounded snippet is the
 * only way to tell that apart from a BaaS business error; without it the caller
 * sees a bare "400" and cannot act. JSON bodies are excluded on purpose so a
 * validation echo can never reach the error message.
 */
function nonJsonBodyDetails(fallback: {
  rawBody: string;
  bodyWasJson: boolean;
  contentType?: string | null;
}): string {
  if (fallback.bodyWasJson) return "";
  const type = errorText(fallback.contentType);
  const snippet = errorText(fallback.rawBody);
  return [type, snippet ? snippet.slice(0, NON_JSON_SNIPPET_CHARS) : ""].filter(Boolean).join(" ");
}

function safeError(message: string, config: { apiKey: string; iamtoken: string }): string {
  // Literal replacement covers credentials that generic patterns cannot recognize.
  // Do this before truncation so a credential spanning the limit cannot leak a prefix.
  for (const secret of [config.apiKey, config.iamtoken].filter(Boolean).sort((a, b) => b.length - a.length)) {
    message = message.replaceAll(secret, "[REDACTED]");
  }
  // Reserve room within 1KB for redactText's truncation marker and UTF-8 boundary.
  return redactText(message, 1000);
}

/**
 * Build request headers for BaaS OpenAPI calls.
 *
 * On macOS (local dev): requires both apiKey + iamtoken from application.yaml.
 * On server (Linux etc.): apiKey only; iamtoken is not needed and ignored.
 */
function buildBaasHeaders(config: { apiKey: string; iamtoken: string; baseUrl: string }): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${config.apiKey}`,
  };
  if (requiresIamToken(config.baseUrl) && config.iamtoken) {
    headers["Cookie"] = `iam_token=${config.iamtoken}`;
  }
  return headers;
}

/**
 * Send an intervention message to the originating bot's session via BaaS.
 *
 * Retries up to 3 times on 5xx errors or network failures.
 */
export async function sendIntervention(params: InterventionParams): Promise<InterventionResult> {
  const { botId, sessionId, message } = params;
  const config = params.transportConfig ?? resolveBaasConfig();

  if (!config.apiKey) {
    return {
      ok: false,
      error: "BaaS apiKey 未配置。请在 application.yaml 中设置 baas.apiKey",
      tokenExpired: false,
    };
  }

  // iamtoken is only required on macOS (local dev)
  if (requiresIamToken(config.baseUrl) && !config.iamtoken) {
    return {
      ok: false,
      error: "BaaS iamtoken 未配置。请在 application.yaml 中设置 baas.iamtoken (macOS 本地开发需要)",
      tokenExpired: false,
    };
  }

  // Fail fast instead of forwarding a bare id: BaaS answers that with a 400
  // that carries no detail, which reads as an unexplained gateway failure.
  if (!BAAS_BOT_ID_PATTERN.test(botId)) {
    return {
      ok: false,
      error: `BaaS bot_id 格式非法: "${botId}"。Open API 要求 "bot_id:owner_staff_no"（示例 default:151614），`
        + "且目标 Bot 必须处于 ACTIVE 且有有效绑定",
      tokenExpired: false,
    };
  }

  const url = `${config.baseUrl}/openapi/v1/messages`;

  const headers = buildBaasHeaders(config);

  const body = {
    bot_id: botId,
    message,
    metadata: {
      // Only an existing session may be addressed. BaaS creates a new session
      // when session_id is absent; sending a synthesized key targeted a session
      // that never existed and was rejected.
      ...(sessionId ? { session_id: sessionId } : {}),
      ...(params.lifecycleStage ? { bot_options: { lifecycle_stage: params.lifecycleStage } } : {}),
      sender_options: {
        from: "owner",
      },
    },
  };

  // Retry up to 3 times on transient failures
  const maxRetries = 3;
  let lastError: string | undefined;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15_000);

    try {
      const res = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      // Read once as text so a non-JSON body can still be described below.
      let rawBody = "";
      try {
        rawBody = await res.text();
      } catch {
        rawBody = "";
      }

      let responseJson: BaasMessageResponse = {};
      let bodyWasJson = false;
      try {
        const parsed: unknown = JSON.parse(rawBody);
        bodyWasJson = true;
        if (isRecord(parsed)) responseJson = parsed as BaasMessageResponse;
      } catch {
        // Non-JSON body: an intermediary answered instead of BaaS.
      }

      const details = errorDetails(responseJson)
        || nonJsonBodyDetails({ rawBody, bodyWasJson, contentType: res.headers.get("content-type") });
      const statusDetail = `${res.status}${details ? ` ${details}` : ""}`;

      // Check for token expiry in BaaS response
      const baasErrorCode = responseJson.buserviceErrorCode;
      if (typeof baasErrorCode === "string" && TOKEN_EXPIRED_CODES.includes(baasErrorCode)) {
        return {
          ok: false,
          error: safeError(`IAM Token 已过期或无效 (HTTP ${statusDetail})`, config),
          tokenExpired: true,
        };
      }

      if (res.ok && responseJson.data) {
        return {
          ok: true,
          messageId: responseJson.data.message_id,
          sessionId: responseJson.data.session_id,
        };
      }

      // Non-retryable client error (4xx except 429)
      if (res.status >= 400 && res.status < 500 && res.status !== 429) {
        return {
          ok: false,
          error: safeError(`BaaS API 错误: ${statusDetail}`, config),
        };
      }

      // 5xx or 429 — retry
      lastError = `BaaS API 返回 ${statusDetail} (attempt ${attempt}/${maxRetries})`;

      // Exponential backoff: 2s, 4s
      if (attempt < maxRetries) {
        await new Promise((r) => setTimeout(r, 2000 * attempt));
      }
    } catch (err) {
      if (controller.signal.aborted) {
        lastError = `BaaS 请求超时 (attempt ${attempt}/${maxRetries})`;
      } else {
        const msg = err instanceof Error ? err.message : String(err);
        lastError = `BaaS 网络错误: ${msg} (attempt ${attempt}/${maxRetries})`;
      }

      if (attempt < maxRetries) {
        await new Promise((r) => setTimeout(r, 2000 * attempt));
      }
    } finally {
      clearTimeout(timeout);
    }
  }

  return {
    ok: false,
    error: safeError(lastError ?? "BaaS 服务暂时不可用，请稍后重试", config),
  };
}
