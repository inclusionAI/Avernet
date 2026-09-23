import { createHash } from "node:crypto";
import type { BotRuntime, BotRuntimeProvider, BotTargetResolver, RuntimeTarget, ResolvedBotTarget, ShellInput, HttpInput, MessageInput } from "./contracts.js";
import { BotRuntimeError } from "./errors.js";

/** Public routing owns provider selection; hosts supply actual implementations. */
export class BotRuntimeClient implements BotRuntime {
  constructor(private readonly options: { targets?: BotTargetResolver; providers: Readonly<Record<string, BotRuntimeProvider>> }) {}
  private async resolve(input: RuntimeTarget): Promise<{ target: ResolvedBotTarget; provider: BotRuntimeProvider }> {
    if (!input.ownerId || !input.botId || !["dev", "pre", "prod"].includes(input.environment)) {
      throw new BotRuntimeError(422, "invalid_bot_target", "Bot target identity is incomplete");
    }
    const target = "provider" in input ? input : await this.options.targets?.resolve(input);
    if (!target) throw new BotRuntimeError(503, "bot_target_unavailable", "Bot target resolver is unavailable");
    if (target.ownerId !== input.ownerId || target.botId !== input.botId || target.environment !== input.environment) {
      throw new BotRuntimeError(409, "bot_target_mismatch", "Resolved Bot does not match requested identity");
    }
    const provider = Object.hasOwn(this.options.providers, target.provider) ? this.options.providers[target.provider] : undefined;
    if (!provider) throw new BotRuntimeError(422, "unsupported_runtime_provider", "Bot runtime provider is unavailable");
    return { target, provider };
  }
  private timeout(value: number | undefined) {
    if (value !== undefined && (!Number.isSafeInteger(value) || value < 1 || value > 600000)) throw new BotRuntimeError(422, "invalid_runtime_timeout", "Timeout must be between 1 and 600000 ms");
  }
  async executeShell(input: ShellInput) {
    this.timeout(input.timeoutMs);
    if (!input.command.trim() || input.command.includes("\0")) throw new BotRuntimeError(422, "invalid_shell_command", "Shell command is invalid");
    const { target, provider } = await this.resolve(input.target);
    return provider.executeShell({ ...input, target });
  }
  async request(input: HttpInput) {
    this.timeout(input.timeoutMs);
    if (!input.path.startsWith("/") || input.path.startsWith("//") || /[\\\r\n\0#]/u.test(input.path)
      || !["GET", "POST", "PUT", "PATCH", "DELETE"].includes(input.method)
      || (input.port !== undefined && (!Number.isSafeInteger(input.port) || input.port < 1 || input.port > 65535))) {
      throw new BotRuntimeError(422, "invalid_runtime_request", "Invalid container HTTP request");
    }
    const { target, provider } = await this.resolve(input.target);
    return provider.request({ ...input, target });
  }
  async sendMessage(input: MessageInput) {
    if (!input.message.trim() || !input.sessionKey.trim() || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(input.deliveryKey)) {
      throw new BotRuntimeError(422, "invalid_runtime_message", "Invalid session message");
    }
    const { target, provider } = await this.resolve(input.target);
    if (input.expectedEngine !== undefined && input.expectedEngine !== target.activeEngine) throw new BotRuntimeError(422, "bot_engine_mismatch", "Bot Engine does not match the requested target snapshot");
    if (target.activeEngine !== undefined && target.activeEngine !== "openclaw") throw new BotRuntimeError(422, "unsupported_engine", "Session messages require OpenClaw");
    const hash = createHash("sha256").update(JSON.stringify([target.ownerId, target.botId, target.environment, input.sessionKey, input.deliveryKey])).digest("hex");
    const deliveryId = `${hash.slice(0,8)}-${hash.slice(8,12)}-${hash.slice(12,16)}-${hash.slice(16,20)}-${hash.slice(20,32)}`;
    // Never repeat a send after an uncertain transport outcome.
    return provider.sendMessage({ ...input, target, deliveryId });
  }
}
