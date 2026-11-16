// Read-only compatibility for existing recovery configuration. Never write these policies here.
import type { AgentControlPlaneEnvironment } from '../common/task-control-plane.js';
import type { MonitoringTarget } from '../../monitoring/contracts.js';
import { target, targetKey } from '../../monitoring/target.js';

export type RecoveryAppConfigStore = { findByKey(key: string): Promise<{ enabled: number; config_json: string } | null> };
export const SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY = "session_recovery_owner_notifications";
export const SESSION_RECOVERY_NOTIFICATIONS_KEY = "session_recovery_notifications";

export const SESSION_RECOVERY_ALLOWLIST_KEY = "session_recovery_allowlist";

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function parseLegacyRecoveryAllowlist(value: unknown): MonitoringTarget[] {
  if (!record(value) || Object.keys(value).some(key => key !== "allowed_bots") || !Array.isArray(value.allowed_bots)) {
    throw new Error("Invalid recovery allowlist");
  }
  const seen = new Set<string>();
  return value.allowed_bots.map(item => {
    if (!record(item) || Object.keys(item).length !== 3
      || Object.keys(item).some(key => !["bot_id", "entity_id", "env"].includes(key))
      || typeof item.bot_id !== "string" || !/^[A-Za-z0-9_-]+$/.test(item.bot_id)
      || typeof item.entity_id !== "string" || !/^[A-Za-z0-9_.@+-]+$/.test(item.entity_id) || item.entity_id.includes("..")
      || (item.env !== "pre" && item.env !== "prod")) throw new Error("Invalid recovery target");
    const entry = target({ botId: item.bot_id, entityId: item.entity_id, env: item.env });
    const key = targetKey(entry);
    if (seen.has(key)) throw new Error("Duplicate recovery target");
    seen.add(key);
    return entry;
  });
}

export function cardConfiguration(value: unknown): Partial<Record<AgentControlPlaneEnvironment, { template_id: string; callback_route_key?: string }>> {
  if (value === undefined) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid recovery card configuration");
  for (const [environment, item] of Object.entries(value)) {
    if (!["pre", "prod"].includes(environment) || !item || typeof item !== "object" || Array.isArray(item)
      || Object.keys(item).some(key => !["template_id", "callback_route_key"].includes(key))
      || typeof item.template_id !== "string" || !item.template_id.trim() || item.template_id.length > 256
      || item.callback_route_key !== undefined && (typeof item.callback_route_key !== "string" || !item.callback_route_key.trim() || item.callback_route_key.length > 256)) throw new Error("Invalid recovery card configuration");
  }
  return value;
}

export function legacyOwnerNotificationEnabled(raw: string, enabled: number, input: { botId: string; ownerId: string; targetEnvironment: string }): boolean {
  let value;
  try { value = JSON.parse(raw); }
  catch { throw new Error("Invalid recovery owner notification JSON"); }
  if (!value || typeof value !== "object" || Array.isArray(value)
    || Object.keys(value).some(key => !["bot_overrides", "card"].includes(key))
    || value.bot_overrides !== undefined && !Array.isArray(value.bot_overrides)) {
    throw new Error("Invalid recovery owner notification configuration");
  }
  cardConfiguration(value.card);
  const targets = new Set<string>();
  let matched: boolean | undefined;
  for (const item of value.bot_overrides ?? []) {
    if (!item || typeof item !== "object" || Array.isArray(item)
      || Object.keys(item).some(key => !["bot_id", "entity_id", "env", "enabled"].includes(key))
      || typeof item.bot_id !== "string" || !item.bot_id.trim() || item.bot_id !== item.bot_id.trim()
      || typeof item.entity_id !== "string" || !item.entity_id.trim() || item.entity_id !== item.entity_id.trim()
      || !["pre", "prod"].includes(item.env) || typeof item.enabled !== "boolean") {
      throw new Error("Invalid recovery owner notification Bot override");
    }
    const key = JSON.stringify([item.bot_id, item.entity_id, item.env]);
    if (targets.has(key)) throw new Error("Duplicate recovery owner notification Bot override");
    targets.add(key);
    if (item.bot_id === input.botId && item.entity_id === input.ownerId && item.env === input.targetEnvironment) matched = item.enabled;
  }
  return matched ?? (enabled === 1);
}

type BotWebhook = { bot_id: string; entity_id: string; webhook: string };
type WebhookConfig = { webhook: string; timeout_seconds: number; bot_webhooks?: BotWebhook[] };

export function legacyWebhookConfig(raw: string): WebhookConfig {
  const value = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)
    || Object.keys(value).some(key => !["webhook", "timeout_seconds", "bot_webhooks"].includes(key))
    || typeof value.webhook !== "string" || new URL(value.webhook).protocol !== "https:"
    || !Number.isSafeInteger(value.timeout_seconds) || value.timeout_seconds < 1 || value.timeout_seconds > 60) {
    throw new Error("Invalid recovery notification configuration");
  }
  if (value.bot_webhooks !== undefined) {
    if (!Array.isArray(value.bot_webhooks)) throw new Error("Invalid recovery Bot notification configuration");
    const targets = new Set<string>();
    for (const item of value.bot_webhooks) {
      if (!item || typeof item !== "object" || Array.isArray(item)
        || Object.keys(item).some(key => !["bot_id", "entity_id", "webhook"].includes(key))
        || typeof item.bot_id !== "string" || !item.bot_id.trim() || item.bot_id !== item.bot_id.trim()
        || typeof item.entity_id !== "string" || !item.entity_id.trim() || item.entity_id !== item.entity_id.trim()
        || typeof item.webhook !== "string" || new URL(item.webhook).protocol !== "https:") {
        throw new Error("Invalid recovery Bot notification configuration");
      }
      const target = JSON.stringify([item.bot_id, item.entity_id]);
      if (targets.has(target)) throw new Error("Duplicate recovery Bot notification target");
      targets.add(target);
    }
  }
  return value;
}

