import { parseBotRuntimeIdentity } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryError } from "./errors.js";
import { containsSecret } from "@avernet/clawweb-shared/server/services/redaction";
import type { RecoveryRequest } from "./contracts.js";
import { cronRunSessionId } from "./session-identity.js";

function invalid(message: string): never { throw new SessionRecoveryError(422, "invalid_session_recovery", message); }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid("需要 JSON 对象");
  return value as Record<string, unknown>;
}
function text(value: unknown, field: string, max: number): string {
  if (typeof value !== "string" || !value.trim() || Array.from(value).length > max || value.includes("\0")) invalid(`${field} 无效`);
  if (containsSecret(value)) invalid(`${field} 不得包含凭据`);
  return value;
}
export function parseRequest(raw: unknown): RecoveryRequest {
  const body = object(raw), target = object(body.target);
  for (const key of Object.keys(body)) if (!["event_id", "tc_fault_label", "diagnosis", "target", "session_key", "session_id", "delivery_key", "error_location", "execution_level"].includes(key)) invalid(`未知字段 ${key}`);
  for (const key of Object.keys(target)) if (!["bot_id", "entity_id", "env", "engine", "runtime_identity"].includes(key)) invalid(`未知 target 字段 ${key}`);
  if (target.env !== "pre" && target.env !== "prod") invalid("target.env 必须是 pre 或 prod");
  if (target.engine !== "OC" && target.engine !== "CC" && target.engine !== "TE") invalid("target.engine 必须是 OC、CC 或 TE");
  let runtimeIdentity;
  try { runtimeIdentity = parseBotRuntimeIdentity(target.runtime_identity); } catch { invalid("target.runtime_identity 无效"); }
  const botId = text(target.bot_id, "target.bot_id", 128), entityId = text(target.entity_id, "target.entity_id", 128);
  if (!/^[A-Za-z0-9_-]+$/.test(botId) || !/^[A-Za-z0-9_.@+-]+$/.test(entityId) || entityId.includes("..")) invalid("Bot 身份无效");
  const label = body.tc_fault_label === null ? null : text(body.tc_fault_label, "tc_fault_label", 128);
  const deliveryKey = text(body.delivery_key, "delivery_key", 128);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(deliveryKey)) invalid("delivery_key 必须为 1–128 位字母、数字或 _ . : -");
  const sessionKey = text(body.session_key, "session_key", 1024);
  if (/[^\S ]/.test(sessionKey) || /[\r\n]/.test(sessionKey)) invalid("session_key 不得包含控制字符");
  if (target.engine === "OC" && !/^agent:[a-zA-Z0-9_-]+:.+/.test(sessionKey)) invalid("需要完整 session_key");
  const sessionId = body.session_id === undefined ? undefined : text(body.session_id, "session_id", 255);
  if (sessionId !== undefined && !/^[A-Za-z0-9_-]+$/.test(sessionId)) invalid("session_id 无效");
  const cronId = target.engine === "OC" ? cronRunSessionId(sessionKey) : undefined;
  if (sessionId && cronId && sessionId !== cronId) invalid("session_id 与 Cron session_key 不一致");
  const errorLocation = body.error_location === undefined ? undefined : text(body.error_location, "error_location", 2048);
  const level = body.execution_level;
  if (level !== undefined && level !== "debug" && level !== "approval" && level !== "automatic") invalid("execution_level 必须是 debug、approval 或 automatic");
  return { ...(errorLocation === undefined ? {} : { error_location: errorLocation }),
    ...(level === undefined ? {} : { execution_level: level }), event_id: text(body.event_id, "event_id", 128), tc_fault_label: label, delivery_key: deliveryKey,
    diagnosis: text(body.diagnosis, "diagnosis", 8000), ...(sessionId === undefined ? {} : { session_id: sessionId }),
    target: { bot_id: botId, entity_id: entityId, env: target.env, engine: target.engine, ...(runtimeIdentity ? { runtime_identity: runtimeIdentity } : {}) }, session_key: sessionKey };
}
