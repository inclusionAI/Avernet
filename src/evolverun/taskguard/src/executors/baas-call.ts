import type {
  WorkflowNode,
  ExecutorResult,
  FlowState,
  BaasCallExecutor,
} from "../types.js";
import type { TemplateContext } from "../runner.js";
import { resolveTemplate } from "../runner.js";
import { loadConfig } from "../config/index.js";

type BaasRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

type BaasSubmitResponse = {
  code?: number;
  message?: string;
  data?: {
    run_id?: string;
    message_id?: string;
    session_id?: string;
  };
  trace_id?: string;
  buserviceErrorCode?: string;
  buserviceErrorMsg?: string;
};

type BaasPollResponse = {
  code?: number;
  message?: string;
  data?: {
    run_id?: string;
    message_id?: string;
    session_id?: string;
    status?: BaasRunStatus;
    result?: { content?: string };
  };
  trace_id?: string;
  buserviceErrorCode?: string;
  buserviceErrorMsg?: string;
};

async function baasFetch(
  url: string,
  options: RequestInit,
): Promise<{ ok: boolean; status: number; body: unknown }> {
  const res = await fetch(url, options);
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = {};
  }
  return { ok: res.ok, status: res.status, body };
}

async function submitWithRetry(
  url: string,
  options: RequestInit,
  maxRetries: number,
): Promise<{ ok: boolean; status: number; body: unknown }> {
  let lastResult: { ok: boolean; status: number; body: unknown } = {
    ok: false,
    status: 0,
    body: {},
  };
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      lastResult = await baasFetch(url, options);
      if (lastResult.ok || lastResult.status < 500) return lastResult;
    } catch {
      // network error — retry
    }
    if (attempt < maxRetries) {
      await new Promise((r) => setTimeout(r, 2000 * (1 << attempt)));
    }
  }
  return lastResult;
}

export type BaasProgressCallback = (message: string) => void;

export async function executeBaasCall(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  _api: unknown,
  _flowState: FlowState,
  onProgress?: BaasProgressCallback,
): Promise<ExecutorResult> {
  if (node.executor.type !== "baas-call") {
    return { status: "failed", error: "not a baas-call node" };
  }

  const executor = node.executor as BaasCallExecutor;
  const mode = executor.mode ?? "run";
  const message = resolveTemplate(executor.message, templateCtx);

  // BaaS credentials resolution. Priority per credential:
  //   1) app config `baas:` section (from local application.yaml, overridden by
  //      clawweb cm_app_config config_key="baas" via deepMerge in initConfig())
  //   2) executor fields in the workflow YAML
  //   3) environment variables as fallback
  // This matches production where secrets live in clawweb's application config DB.
  const baasCfg = loadConfig().app.baas;
  const baseUrl = baasCfg.baseUrl || executor.baseUrl || process.env.BAAS_BASE_URL || "";
  const timeoutMs = executor.timeoutMs ?? (executor.timeoutSeconds ? executor.timeoutSeconds * 1000 : 120_000);
  const pollIntervalMs = executor.pollIntervalMs ?? 3_000;

  // API key resolution. Precedence:
  //   config.baas.apiKey (application.yaml / clawweb cm_app_config) →
  //   env.BAAS_API_KEY → apiKeyRef (legacy: a literal secret or env name).
  const apiKeyRef = executor.apiKeyRef ?? "BAAS_API_KEY";
  const apiKey =
    baasCfg.apiKey ||
    process.env.BAAS_API_KEY ||
    (apiKeyRef && apiKeyRef !== "BAAS_API_KEY" ? apiKeyRef : "") ||
    "";

  if (!baseUrl) {
    return {
      status: "failed",
      error:
        "baas-call requires a base URL: configure the `baas:` section in application config " +
        "(or clawweb cm_app_config config_key=\"baas\"), set `baseUrl` in the workflow YAML, " +
        "or set the BAAS_BASE_URL env var",
    };
  }
  if (!apiKey) {
    return {
      status: "failed",
      error:
        "baas-call requires an API key: configure `apiKey` under the `baas:` config section " +
        "(or clawweb cm_app_config config_key=\"baas\"), or set the BAAS_API_KEY env var",
    };
  }

  if (mode === "message" && !executor.botId) {
    return {
      status: "failed",
      error: "baas-call in message mode requires botId",
    };
  }

  const resolvedBotId = executor.botId
    ? resolveTemplate(executor.botId, templateCtx)
    : undefined;

  // Build submit request
  const submitUrl =
    mode === "message"
      ? `${baseUrl}/openapi/v1/messages`
      : `${baseUrl}/openapi/v1/runs`;

  const submitBody: Record<string, unknown> = { message };
  if (mode === "message" && resolvedBotId) {
    submitBody.bot_id = resolvedBotId;
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${apiKey}`,
  };

  // Office network uses IAM token via Cookie header per BaaS Open API doc.
  // Precedence: config.baas.iamToken → workflow YAML executor.iamToken.
  const iamTokenRaw = baasCfg.iamToken || executor.iamToken || undefined;
  const iamToken = iamTokenRaw
    ? resolveTemplate(iamTokenRaw, templateCtx)
    : undefined;
  if (iamToken) {
    headers["Cookie"] = `iam_token=${iamToken}`;
  }

  // Submit with retry
  const submitResult = await submitWithRetry(
    submitUrl,
    { method: "POST", headers, body: JSON.stringify(submitBody) },
    3,
  );

  const submitResp = submitResult.body as BaasSubmitResponse;

  // Detect auth failures — BaaS may return HTTP 200 with error body
  const buserviceError = submitResp.buserviceErrorCode;
  if (buserviceError === "USER_NOT_LOGIN" || buserviceError === "TOKEN_INVALID" || buserviceError === "TOKEN_EXPIRED") {
    const authMsg = submitResp.buserviceErrorMsg ?? submitResp.message ?? "unknown auth error";
    return {
      status: "failed",
      error: `BaaS API auth failed (${buserviceError}): ${authMsg}.`,
    };
  }

  if (!submitResult.ok) {
    const errorCode = submitResp.code;
    const errorMsg = submitResp.message ?? `HTTP ${submitResult.status}`;

    if (
      errorCode === 40101 ||
      errorCode === 40102 ||
      errorCode === 40103 ||
      submitResult.status === 401
    ) {
      return { status: "failed", error: "BaaS API authentication failed" };
    }
    return {
      status: "failed",
      error: `BaaS API submit failed: ${errorMsg}`,
    };
  }

  // Check for non-zero code in response body (API-level error)
  if (submitResp.code !== undefined && submitResp.code !== 0) {
    return {
      status: "failed",
      error: `BaaS API returned error code ${submitResp.code}: ${submitResp.message ?? "unknown error"}`,
    };
  }

  const runId = submitResp.data?.run_id ?? submitResp.data?.message_id;
  if (!runId) {
    // Log the full response body for debugging
    const bodyPreview = JSON.stringify(submitResp).slice(0, 500);
    return {
      status: "failed",
      error: `BaaS API submit returned no run_id/message_id. Response: ${bodyPreview}`,
    };
  }
  const sessionId = submitResp.data?.session_id;

  // Emit progress with runId so frontend can poll BaaS status
  if (onProgress) {
    const progressMsg = mode === "message"
      ? `正在调用 ${resolvedBotId ?? "bot"}… (messageId=${runId})`
      : `正在调用 ${resolvedBotId ?? "bot"}… (runId=${runId})`;
    onProgress(progressMsg);
  }

  // Poll for result
  const pollUrl =
    mode === "message"
      ? `${baseUrl}/openapi/v1/messages/${runId}`
      : `${baseUrl}/openapi/v1/runs/${runId}`;

  const startTime = Date.now();
  let interval = pollIntervalMs;
  const maxInterval = 15_000;

  while (true) {
    const elapsed = Date.now() - startTime;
    if (elapsed >= timeoutMs) {
      return {
        status: "failed",
        error: `BaaS call timed out after ${timeoutMs}ms (${mode === "message" ? "messageId" : "runId"}: ${runId})`,
      };
    }

    await new Promise((r) => setTimeout(r, interval));
    interval = Math.min(interval * 2, maxInterval);

    try {
      const pollResult = await baasFetch(pollUrl, {
        method: "GET",
        headers,
      });

      const pollResp = pollResult.body as BaasPollResponse;
      const statusUpper = pollResp?.data?.status?.toUpperCase();

      if (statusUpper === "COMPLETED") {
        const content = pollResp?.data?.result?.content ?? "";
        return {
          status: "succeeded",
          result: { status: "COMPLETED", content, runId, sessionId },
        };
      }

      if (statusUpper === "FAILED") {
        const content = pollResp?.data?.result?.content ?? "";
        return {
          status: "succeeded",
          result: { status: "FAILED", content, runId, sessionId },
        };
      }

      // PENDING or RUNNING — continue polling
    } catch {
      // network error on poll — continue polling up to timeout
    }
  }
}