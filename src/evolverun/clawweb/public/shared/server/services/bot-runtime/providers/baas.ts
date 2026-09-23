import { runtimeResponse } from "../internal/http-response.js";
import type { ResolvedBaasConfig } from "../../../db.js";
import type { BotRuntimeProvider, ResolvedShellInput, ResolvedHttpInput, ResolvedMessageInput, ShellResult, MessageReceipt } from "../contracts.js";
import { BotRuntimeError } from "../errors.js";
import { executeBaasCommand, resolveBaasCommandTarget, baasRequestHeaders } from "../internal/baas-command-transport.js";

/** Public BaaS protocol implementation; endpoints and credentials come from the host. */
export class BaasRuntimeProvider implements BotRuntimeProvider {
  constructor(private readonly config: ResolvedBaasConfig) {}
  async executeShell(input: ResolvedShellInput): Promise<ShellResult> {
    const config = resolveBaasCommandTarget(this.config, input.target.environment);
    const timeoutMs = input.timeoutMs ?? (this.config.commandTimeoutSeconds || 30) * 1000;
    try {
      const { response, body } = await executeBaasCommand({ config, tenant: this.config.commandTenant,
        deviceId: input.target.deviceId, deviceAffinity: input.deviceAffinity ?? input.target.ownerId,
        cmd: input.command, timeoutSeconds: Math.ceil(timeoutMs / 1000), signal: AbortSignal.timeout(timeoutMs + 5000) });
      const data = body.data ?? {}, code = data.exit_code ?? data.result?.exit_code ?? null;
      const rejected = !response.ok || (body.code != null && Number(body.code) !== 0)
        || (body.buserviceErrorCode != null && ![0, "0", ""].includes(body.buserviceErrorCode));
      return { status: rejected ? "failed" : body.data == null ? "unknown" : code == null || code === 0 ? "success" : "failed",
        exitCode: code, stdout: data.stdout ?? data.result?.stdout ?? "", stderr: data.stderr ?? data.result?.stderr ?? "",
        durationMs: data.execution_time_ms ?? null };
    } catch {
      return { status: "unknown", exitCode: null, stdout: "", stderr: "", durationMs: null, error: "Shell outcome is unknown; not retried" };
    }
  }
  async request(input: ResolvedHttpInput) {
    const config = resolveBaasCommandTarget(this.config, input.target.environment);
    const headers = baasRequestHeaders(config), signal = AbortSignal.timeout(input.timeoutMs ?? 30000);
    let url: string, requestHeaders: Record<string, string>;
    if (input.target.botType === "desktop") {
      url = `${config.baseUrl}/api/v1/bots/${encodeURIComponent(this.config.commandTenant)}/${encodeURIComponent(input.target.deviceId)}/invoke-http/${input.port ?? 20003}${input.path}`;
      requestHeaders = headers;
    } else {
      const query = new URLSearchParams({ tenant: this.config.commandTenant, port: String(input.port ?? 20003), path: input.path, device_affinity: input.target.ownerId });
      const response = await fetch(`${config.baseUrl}/api/v1/bots/${encodeURIComponent(input.target.deviceId)}/http-info?${query}`, { headers, signal, redirect: "error" });
      const info = await response.json();
      if (!response.ok || Number(info.code) !== 0 || typeof info.data?.http_url !== "string" || typeof info.data?.token !== "string" || !info.data.token) {
        throw new BotRuntimeError(502, "baas_connection_unavailable", "BaaS did not return a usable HTTP connection");
      }
      const parsed = new URL(info.data.http_url);
      if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.hash) throw new BotRuntimeError(502, "baas_connection_invalid", "Invalid BaaS HTTP connection");
      url = parsed.href;
      requestHeaders = { "Content-Type": "application/json", [parsed.pathname.startsWith("/proxypass/") ? "x-proxypass-token" : "openclawToken"]: info.data.token };
    }
    const response = await fetch(url, { method: input.method, headers: requestHeaders, body: input.body === undefined ? undefined : JSON.stringify(input.body), signal, redirect: "error" });
    return runtimeResponse(response);
  }
  async sendMessage(input: ResolvedMessageInput): Promise<MessageReceipt> {
    const config = resolveBaasCommandTarget(this.config, input.target.environment);
    const suffix = `:${input.target.ownerId}`, botId = input.target.botId.endsWith(suffix) ? input.target.botId : input.target.botId + suffix;
    try {
      const response = await fetch(`${config.baseUrl}/openapi/v1/messages`, { method: "POST", headers: baasRequestHeaders(config), redirect: "error",
        body: JSON.stringify({ bot_id: botId, message: input.message, message_id: input.deliveryKey,
          metadata: { session_id: input.sessionKey, sender_options: { from: "owner" } } }), signal: AbortSignal.timeout(60000) });
      const body = await response.json().catch(() => null);
      if (!response.ok || (body?.code != null && ![0, "0"].includes(body.code))
        || (body?.buserviceErrorCode != null && ![0, "0", ""].includes(body.buserviceErrorCode))) {
        return { status: response.status >= 400 && response.status < 500 && response.status !== 408 ? "failed" : "unknown", error: "Message was not confirmed; not retried" };
      }
      if (typeof body?.data?.message_id !== "string" || !body.data.message_id.trim()) return { status: "unknown", error: "Message receipt is missing" };
      return { status: "accepted", acceptedAt: Date.now(), platformMessageId: body.data.message_id,
        ...(typeof body.data.session_id === "string" ? { sessionId: body.data.session_id } : {}), locationStatus: "unconfirmed" };
    } catch { return { status: "unknown", error: "Message outcome is unknown; not retried" }; }
  }
}
