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

/** Whether the current platform requires iamtoken for BaaS calls */
const PLATFORM_REQUIRES_IAMTOKEN = process.platform === "darwin";

export type InterventionParams = {
  /** BaaS-format bot_id: "real_bot_id:staff_no" (e.g. "default:151614") */
  botId: string;
  /** Full sessionKey (e.g. "agent:main:dashboard:xxx-yyy") */
  sessionKey: string;
  /** Resolved session UUID for BaaS routing (e.g. "74313981-7237-45c3-a949-a899b959afc2") */
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
};

/** BaaS error codes indicating token issues */
const TOKEN_EXPIRED_CODES = ["TOKEN_EXPIRED", "TOKEN_INVALID", "USER_NOT_LOGIN"];

/**
 * Build request headers for BaaS OpenAPI calls.
 *
 * On macOS (local dev): requires both apiKey + iamtoken from application.yaml.
 * On server (Linux etc.): apiKey only; iamtoken is not needed and ignored.
 */
function buildBaasHeaders(config: { apiKey: string; iamtoken: string }): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${config.apiKey}`,
  };
  if (PLATFORM_REQUIRES_IAMTOKEN && config.iamtoken) {
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
  const { botId, sessionKey, sessionId, message } = params;
  const config = params.transportConfig ?? resolveBaasConfig();

  if (!config.apiKey) {
    return {
      ok: false,
      error: "BaaS apiKey 未配置。请在 application.yaml 中设置 baas.apiKey",
      tokenExpired: false,
    };
  }

  // iamtoken is only required on macOS (local dev)
  if (PLATFORM_REQUIRES_IAMTOKEN && !config.iamtoken) {
    return {
      ok: false,
      error: "BaaS iamtoken 未配置。请在 application.yaml 中设置 baas.iamtoken (macOS 本地开发需要)",
      tokenExpired: false,
    };
  }

  const url = `${config.baseUrl}/openapi/v1/messages`;

  const headers = buildBaasHeaders(config);

  const body = {
    bot_id: botId,
    message,
    metadata: {
      session_id: sessionId ?? sessionKey,
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

      let responseJson: BaasMessageResponse;
      try {
        responseJson = (await res.json()) as BaasMessageResponse;
      } catch {
        responseJson = {};
      }

      // Check for token expiry in BaaS response
      const baasErrorCode = responseJson.buserviceErrorCode;
      if (baasErrorCode && TOKEN_EXPIRED_CODES.includes(baasErrorCode)) {
        return {
          ok: false,
          error: `IAM Token 已过期或无效 (${baasErrorCode}: ${responseJson.buserviceErrorMsg ?? responseJson.message ?? ""})`,
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
          error: `BaaS API 错误: ${res.status} ${baasErrorCode ?? ""} ${responseJson.buserviceErrorMsg ?? responseJson.message ?? ""}`.trim(),
        };
      }

      // 5xx or 429 — retry
      lastError = `BaaS API 返回 ${res.status} (attempt ${attempt}/${maxRetries})`;

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
    error: lastError ?? "BaaS 服务暂时不可用，请稍后重试",
  };
}
