import { SessionRecoveryError } from "./errors.js";
import { containsSecret } from "@avernet/clawweb-shared/server/services/redaction";
import type { RecoveryRequest } from "./contracts.js";

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
  for (const key of Object.keys(body)) if (!["event_id", "tc_fault_label", "diagnosis", "target", "session_key", "delivery_key"].includes(key)) invalid(`未知字段 ${key}`);
  for (const key of Object.keys(target)) if (!["bot_id", "entity_id", "env", "engine"].includes(key)) invalid(`未知 target 字段 ${key}`);
  if (target.env !== "pre" && target.env !== "prod") invalid("target.env 必须是 pre 或 prod");
  if (target.engine !== "OC" && target.engine !== "TE") invalid("target.engine 必须是 OC 或 TE");
  const botId = text(target.bot_id, "target.bot_id", 128), entityId = text(target.entity_id, "target.entity_id", 128);
  if (!/^[A-Za-z0-9_-]+$/.test(botId) || !/^[A-Za-z0-9_.@+-]+$/.test(entityId) || entityId.includes("..")) invalid("Bot 身份无效");
  const label = body.tc_fault_label === null ? null : text(body.tc_fault_label, "tc_fault_label", 128);
  if (label !== null && !/^TC\.[A-Z_]+\.[A-Z_]+$/.test(label)) invalid("tc_fault_label 必须是 TC 分类标签或 null");
  const deliveryKey = text(body.delivery_key, "delivery_key", 128);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(deliveryKey)) invalid("delivery_key 必须为 1–128 位字母、数字或 _ . : -");
  const sessionKey = text(body.session_key, "session_key", 1024);
  if (/[^\S ]/.test(sessionKey) || /[\r\n]/.test(sessionKey)) invalid("session_key 不得包含控制字符");
  if (target.engine === "OC" && !/^agent:[a-zA-Z0-9_-]+:.+/.test(sessionKey)) invalid("需要完整 session_key");
  return { event_id: text(body.event_id, "event_id", 128), tc_fault_label: label, delivery_key: deliveryKey,
    diagnosis: text(body.diagnosis, "diagnosis", 8000),
    target: { bot_id: botId, entity_id: entityId, env: target.env, engine: target.engine }, session_key: sessionKey };
}
