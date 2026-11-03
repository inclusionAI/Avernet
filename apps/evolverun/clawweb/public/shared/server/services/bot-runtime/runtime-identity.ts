import type { BotRuntimeIdentity } from "./contracts.js";
import { BotRuntimeError } from "./errors.js";

/** Consume runtime locators from a complete identity object; ignore other metadata. */
export function parseBotRuntimeIdentity(input: unknown): BotRuntimeIdentity | undefined {
  if (input === undefined) return undefined;
  const invalid = (): never => { throw new BotRuntimeError(422, "invalid_runtime_identity", "运行身份的阶段、设备或版本字段无效"); };
  if (!input || typeof input !== "object" || Array.isArray(input)) return invalid();
  const value = input as Record<string, unknown>;
  // Personal Bots use their existing binding, not service release/instance locators.
  if (value.BOT_TYPE === "personal") return undefined;
  const locator = (name: string, source: string) => {
    if (value[name] !== undefined && value[source] !== undefined && value[name] !== value[source]) return invalid();
    return value[name] === undefined ? value[source] : value[name];
  };
  const stage = locator("stage", "STAGE"), deviceUuid = locator("deviceUuid", "CLIENT_ID");
  const version = stage === "draft" ? undefined : locator("version", "VERSION");
  if (stage !== "draft" && stage !== "verify" && stage !== "online") return invalid();
  if (deviceUuid !== undefined && (typeof deviceUuid !== "string" || !/^DEVICE-[a-f0-9]{32}$/.test(deviceUuid))) return invalid();
  if (version !== undefined && (typeof version !== "string" || !/^V[1-9][0-9]{0,8}$/.test(version))) return invalid();
  return { stage, ...(deviceUuid === undefined ? {} : { deviceUuid: deviceUuid as string }),
    ...(version === undefined ? {} : { version: version as string }) };
}
