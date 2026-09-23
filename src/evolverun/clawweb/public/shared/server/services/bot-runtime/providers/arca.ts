import { runtimeResponse } from "../internal/http-response.js";
import type { ArcaConnectionProvider, BotRuntimeProvider, ResolvedBotTarget, ResolvedShellInput, ResolvedHttpInput, ResolvedMessageInput, ShellResult } from "../contracts.js";
import { BotRuntimeError } from "../errors.js";
import { ArcaCommandTransport } from "../internal/arca-command-transport.js";
import { sendEngineMessage } from "../internal/engine-message.js";
export type ArcaRuntimeOptions = {
  connectionProvider?: ArcaConnectionProvider;
  proxyBaseUrls: Partial<Record<"pre" | "prod", string>>;
  engineIdentity?: (target: ResolvedBotTarget) => Promise<string> | string;
  prepareMessageCommand?: (command: string) => string;
  engineUrl?: string;
  /** Existing Repair transport can be supplied during caller migration. */
  commandTransport?: Pick<ArcaCommandTransport, "execute">;
};
export class ArcaRuntimeProvider implements BotRuntimeProvider {
  private readonly transport: Pick<ArcaCommandTransport, "execute">;
  constructor(private readonly options: ArcaRuntimeOptions) {
    if (options.commandTransport) this.transport = options.commandTransport;
    else if (options.connectionProvider) this.transport = new ArcaCommandTransport({ ...options, connectionProvider: options.connectionProvider });
    else throw new BotRuntimeError(503, "arca_not_configured", "Arca connection is not configured");
  }
  private target(target: ResolvedBotTarget) {
    if (target.environment === "dev" || !target.sandboxId) throw new BotRuntimeError(422, "invalid_arca_target", "Legacy Arca requires a remote sandbox");
    return { environment: target.environment, bindingId: target.bindingId, sandboxId: target.sandboxId, arcaInstanceId: target.arcaInstanceId };
  }
  async executeShell(input: ResolvedShellInput): Promise<ShellResult> {
    const target = this.target(input.target);
    try {
      return await this.transport.execute({ ...target, command: input.command, timeoutMs: input.timeoutMs });
    } catch (error) {
      if (error instanceof BotRuntimeError && error.status < 500) throw error;
      return { status: "unknown", exitCode: null, stdout: "", stderr: "", durationMs: null, error: "Shell outcome is unknown; not retried" };
    }
  }
  async request(input: ResolvedHttpInput) {
    const target = this.target(input.target), port = input.port ?? 20003;
    if (!this.options.connectionProvider) throw new BotRuntimeError(503, "arca_http_unavailable", "Arca HTTP connection is not configured");
    const connection = await this.options.connectionProvider.getConnection({ ...target, ttlSeconds: 120, port });
    if (connection.target !== `ARCA_${target.arcaInstanceId ?? target.sandboxId}:${port}` || !connection.token) throw new BotRuntimeError(409, "arca_target_mismatch", "Arca connection target changed");
    const base = this.options.proxyBaseUrls[target.environment];
    if (!base) throw new BotRuntimeError(503, "arca_not_configured", "Arca proxy is not configured");
    const origin = new URL(base);
    if (!["http:", "https:"].includes(origin.protocol) || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) throw new BotRuntimeError(503, "arca_not_configured", "Invalid Arca proxy origin");
    const identity = connection.localOwnerIdentity;
    const headers: Record<string, string> = { "Content-Type": "application/json", "x-proxypass-token": connection.token,
      ...(identity ? { Cookie: identity.cookie, "x-user-id": identity.userId } : {}),
      ...(port === 20003 && this.options.engineIdentity ? { "x-iam-token": await this.options.engineIdentity(input.target) } : {}) };
    const response = await fetch(`${origin.origin}/proxypass/${connection.target}${input.path}`, { method: input.method, headers,
      body: input.body === undefined ? undefined : JSON.stringify(input.body), redirect: "error", signal: AbortSignal.timeout(input.timeoutMs ?? 30000) });
    return runtimeResponse(response);
  }
  async sendMessage(input: ResolvedMessageInput) {
    this.target(input.target);
    if (!this.options.engineIdentity) throw new BotRuntimeError(503, "engine_identity_unavailable", "Engine identity is not configured");
    return sendEngineMessage(input, { engineUrl: this.options.engineUrl ?? "ws://127.0.0.1:20003/ws",
      engineAuthToken: await this.options.engineIdentity(input.target), execute: command => this.executeShell({ target: input.target, command: this.options.prepareMessageCommand?.(command) ?? command, timeoutMs: 35000 }) });
  }
}
