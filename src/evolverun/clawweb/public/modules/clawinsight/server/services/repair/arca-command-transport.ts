import { redactPersistableLines } from "@avernet/clawweb-shared/server/services/redaction";
import * as shared from "@avernet/clawweb-shared/server/services/bot-runtime/legacy";
import { BotRuntimeError as BotConnectionError } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { RepairError } from "./errors.js";
export type { ArcaConnectionProvider, SecretValueProvider, ArcaCommandResult, ArcaCommandTransportOptions } from "@avernet/clawweb-shared/server/services/bot-runtime/legacy";
export const ARCA_TERMINAL_PORT = shared.ARCA_TERMINAL_PORT;
function translate(error: unknown): never {
  if (error instanceof BotConnectionError) throw new RepairError(error.status, error.code, error.message);
  throw error;
}
export function normalizeArcaSandboxId(value: unknown) {
  try { return shared.normalizeArcaSandboxId(value); } catch (error) { return translate(error); }
}
export function validateArcaProxyConnection(...args: Parameters<typeof shared.validateArcaProxyConnection>) {
  try { return shared.validateArcaProxyConnection(...args); } catch (error) { return translate(error); }
}
export class DirectArcaConnectionProvider extends shared.DirectArcaConnectionProvider {
  override async getConnection(input: Parameters<shared.DirectArcaConnectionProvider["getConnection"]>[0]) {
    try { return await super.getConnection(input); } catch (error) { return translate(error); }
  }
}
export class ArcaCommandTransport extends shared.ArcaCommandTransport {
  constructor(options: shared.ArcaCommandTransportOptions) {
    super({ ...options, proxyBaseUrls: {
      pre: options.proxyBaseUrls?.pre ?? "https://agentclawproxy-pre.alipay.com",
      prod: options.proxyBaseUrls?.prod ?? "https://agentclawproxy-prod.alipay.com",
    } });
  }
  override async execute(input: Parameters<shared.ArcaCommandTransport["execute"]>[0]) {
    try { const result = await super.execute(input); return { ...result, stdout: redactPersistableLines(result.stdout, 32768), stderr: redactPersistableLines(result.stderr, 32768) }; } catch (error) { return translate(error); }
  }
}
