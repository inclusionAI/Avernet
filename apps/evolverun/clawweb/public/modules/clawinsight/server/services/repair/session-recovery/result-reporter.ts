import type { RecoveryTaskResult } from "./task-contracts.js";
import type { RecoveryResult, RecoveryResultReporter } from "./contracts.js";
import { RecoveryResultCallbackError } from "./errors.js";

const RESULT_PATH = "/api/insight/v1/internal/monitoring/session-recovery-results";
function transportFailure(error: unknown, httpStatus?: number): RecoveryResultCallbackError {
  const cause = error instanceof Error && error.cause && typeof error.cause === "object" ? error.cause as { code?: unknown } : undefined;
  const transportCode = typeof cause?.code === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(cause.code) ? cause.code : undefined;
  const timeout = error instanceof Error && error.name === "TimeoutError"
    || transportCode === "ETIMEDOUT" || transportCode === "UND_ERR_CONNECT_TIMEOUT";
  return new RecoveryResultCallbackError({ errorCode: timeout ? "RECOVERY_RESULT_TIMEOUT" : "RECOVERY_RESULT_NETWORK",
    failureKind: timeout ? "timeout" : "network", reason: timeout ? "结果回传超时" : "结果回传网络请求失败",
    ...(transportCode ? { transportCode } : {}), ...(httpStatus === undefined ? {} : { httpStatus }) });
}
async function responseBody(response: Response): Promise<Record<string, unknown> | null> {
  if (!response.body) return null;
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > 4096) return null;
      chunks.push(chunk.value);
    }
    const body: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    return body && typeof body === "object" && !Array.isArray(body) ? body as Record<string, unknown> : null;
  } catch (error) {
    if (error instanceof SyntaxError) return null;
    throw error;
  } finally { await reader.cancel().catch(() => undefined); }
}
/** One callback attempt. The caller owns retries; callback failure never resends the Bot message. */
export class HttpRecoveryResultReporter implements RecoveryResultReporter {
  private readonly url: URL;
  constructor(private readonly options: { baseUrl: string; timeoutMs?: number }) {
    this.url = new URL(options.baseUrl);
    if (!["https:", "http:"].includes(this.url.protocol) || this.url.username || this.url.password
      || this.url.pathname !== "/" || this.url.search || this.url.hash
      || (this.url.protocol === "http:" && !["127.0.0.1", "localhost", "[::1]"].includes(this.url.hostname))) {
      throw new Error("Session recovery callback requires a trusted HTTPS or loopback origin");
    }
    if (options.timeoutMs !== undefined && (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs < 1 || options.timeoutMs > 60000)) throw new Error("Invalid callback timeout");
    this.url.pathname = RESULT_PATH;
  }
  async report(result: RecoveryResult | RecoveryTaskResult): Promise<void> {
    let response: Response;
    let body: Record<string, unknown> | null;
    let httpStatus: number | undefined;
    const signal = AbortSignal.timeout(this.options.timeoutMs ?? 10000);
    try {
      response = await fetch(this.url, { method: "POST", redirect: "error",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(result), signal });
      httpStatus = response.status;
      body = await responseBody(response);
    } catch (error) { throw transportFailure(signal.aborted ? signal.reason : error, httpStatus); }
    const error = body?.error && typeof body.error === "object" ? body.error as Record<string, unknown> : body;
    const code = error?.code;
    const upstreamCode = typeof code === "string" && /^MONITORING_[A-Z0-9_]{1,64}$/.test(code) ? code : undefined;
    if (!response.ok) throw new RecoveryResultCallbackError({ errorCode: "RECOVERY_RESULT_HTTP_REJECTED",
      failureKind: "http", httpStatus: response.status, ...(upstreamCode ? { upstreamCode } : {}), reason: "结果回传被 HTTP 服务拒绝" });
    if (body?.accepted !== true || body.eventId !== result.event_id
      || ("task_id" in result && (body.taskId !== result.task_id || body.resultVersion !== result.result_version))) {
      throw new RecoveryResultCallbackError({ errorCode: "RECOVERY_RESULT_INVALID_ACK", failureKind: "invalid_ack",
        httpStatus: response.status, ...(upstreamCode ? { upstreamCode } : {}), reason: "结果回传未收到匹配的接收回执" });
    }
  }
}
