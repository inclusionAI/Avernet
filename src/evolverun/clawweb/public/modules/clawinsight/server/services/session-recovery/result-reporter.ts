import type { RecoveryResult, RecoveryResultReporter } from "./contracts.js";
import { SessionRecoveryError } from "./errors.js";

const RESULT_PATH = "/api/insight/v1/internal/monitoring/session-recovery-results";
/** One callback attempt. The caller owns replay; never resend the Bot message on callback failure. */
export class HttpRecoveryResultReporter implements RecoveryResultReporter {
  private readonly url: URL;
  constructor(private readonly options: { baseUrl: string; token: string; timeoutMs?: number }) {
    this.url = new URL(options.baseUrl);
    if (!["https:", "http:"].includes(this.url.protocol) || this.url.username || this.url.password
      || this.url.pathname !== "/" || this.url.search || this.url.hash
      || (this.url.protocol === "http:" && !["127.0.0.1", "localhost", "[::1]"].includes(this.url.hostname))) {
      throw new Error("Session recovery callback requires a trusted HTTPS or loopback origin");
    }
    if (!options.token.trim() || /[\r\n\0]/.test(options.token)) throw new Error("Session recovery callback credential is required");
    if (options.timeoutMs !== undefined && (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs < 1 || options.timeoutMs > 60000)) throw new Error("Invalid callback timeout");
    this.url.pathname = RESULT_PATH;
  }
  async report(result: RecoveryResult): Promise<void> {
    try {
      const response = await fetch(this.url, { method: "POST", redirect: "error",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.options.token}` },
        body: JSON.stringify(result), signal: AbortSignal.timeout(this.options.timeoutMs ?? 10000) });
      await response.body?.cancel();
      if (!response.ok) throw new Error("Callback rejected");
    } catch {
      // Credentials, upstream bodies and URLs must never enter the public error/log.
      throw new SessionRecoveryError(503, "recovery_result_unavailable", "投递结果回传未确认；未重新发送建议消息");
    }
  }
}
