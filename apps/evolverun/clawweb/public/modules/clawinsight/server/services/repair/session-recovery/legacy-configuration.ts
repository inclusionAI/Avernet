import { parseRecoveryHandbookRoutes } from "./handbook.js";
import { SessionRecoveryError } from "./errors.js";
// Read-only compatibility for existing recovery configuration. Never write these policies here.
import type { AgentControlPlaneEnvironment } from '../common/task-control-plane.js';
import type { MonitoringTarget } from '../../monitoring/contracts.js';
import { target, targetKey } from '../../monitoring/target.js';

import { cardConfiguration, type RecoveryAppConfigStore, type RecoveryNotifications } from './configuration-contracts.js';
import type { RecoveryRequest } from './contracts.js';
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

type BotTeam = { env: "pre" | "prod"; bot_id: string; owner_id: string; team_key: string };
type HandbookConfig = { bot_teams: BotTeam[]; team_routes: Record<string, Record<string, string[]>> };

function handbookRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function invalidHandbook(): never {
  throw new SessionRecoveryError(503, "handbook_configuration_invalid", "团队手册配置无效，未创建自愈任务");
}
export function parseLegacyRecoveryHandbooks(raw: string): HandbookConfig {
  let value: unknown;
  try { value = JSON.parse(raw); } catch { invalidHandbook(); }
  if (!handbookRecord(value) || Object.keys(value).some(key => !["bot_teams", "team_routes"].includes(key))
    || !handbookRecord(value.team_routes)) invalidHandbook();
  const teamKey = /^[a-z0-9][a-z0-9_-]{0,63}$/;
  const botTeams: unknown[] = [];
  if (Array.isArray(value.bot_teams)) botTeams.push(...value.bot_teams);
  else {
    if (!handbookRecord(value.bot_teams)) invalidHandbook();
    for (const [team, identities] of Object.entries(value.bot_teams)) {
      if (!teamKey.test(team) || !Object.hasOwn(value.team_routes, team) || !Array.isArray(identities)) invalidHandbook();
      for (const identity of identities) {
        if (typeof identity !== "string") invalidHandbook();
        const parts = identity.split("|");
        if (parts.length !== 3) invalidHandbook();
        const [env, bot_id, owner_id] = parts;
        botTeams.push({ env, bot_id, owner_id, team_key: team });
      }
    }
  }
  const targets = new Set<string>();
  for (const item of botTeams) {
    if (!handbookRecord(item) || Object.keys(item).length !== 4
      || Object.keys(item).some(key => !["env", "bot_id", "owner_id", "team_key"].includes(key))
      || item.env !== "pre" && item.env !== "prod"
      || typeof item.bot_id !== "string" || !/^[A-Za-z0-9_-]+$/.test(item.bot_id)
      || typeof item.owner_id !== "string" || !/^[A-Za-z0-9_.@+-]+$/.test(item.owner_id) || item.owner_id.includes("..")
      || typeof item.team_key !== "string" || !teamKey.test(item.team_key)
      || !Object.hasOwn(value.team_routes, item.team_key)) invalidHandbook();
    const target = JSON.stringify([item.env, item.bot_id, item.owner_id]);
    if (targets.has(target)) invalidHandbook();
    targets.add(target);
  }
  return { bot_teams: botTeams as BotTeam[], team_routes: parseRecoveryHandbookRoutes(value.team_routes) };
}

/** Legacy storage and identity formats end at this adapter. */
export async function legacyAllowedTargets(app?: RecoveryAppConfigStore): Promise<Set<string>> {
  const row = await app?.findByKey(SESSION_RECOVERY_ALLOWLIST_KEY);
  return new Set(row?.enabled === 1 ? parseLegacyRecoveryAllowlist(JSON.parse(row.config_json)).map(targetKey) : []);
}

export async function legacyRecoveryNotifications(target: RecoveryRequest['target'], app?: RecoveryAppConfigStore): Promise<RecoveryNotifications> {
  const [ownerRow, groupRow] = await Promise.all([
    app?.findByKey(SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY), app?.findByKey(SESSION_RECOVERY_NOTIFICATIONS_KEY),
  ]);
  const owner = ownerRow ? legacyOwnerNotificationEnabled(ownerRow.config_json, ownerRow.enabled,
    { botId: target.bot_id, ownerId: target.entity_id, targetEnvironment: target.env }) : false;
  let group: RecoveryNotifications['group'] = null;
  if (groupRow?.enabled === 1) {
    const parsed = legacyWebhookConfig(groupRow.config_json);
    const webhook = parsed.bot_webhooks?.find(item => item.bot_id === target.bot_id && item.entity_id === target.entity_id)?.webhook ?? parsed.webhook;
    group = { webhook, timeout_seconds: parsed.timeout_seconds };
  }
  return { owner, group };
}

export async function legacyRecoveryCardConfiguration(app: RecoveryAppConfigStore, environment: AgentControlPlaneEnvironment) {
  const row = await app.findByKey(SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY);
  // The old row's enabled flag is the Owner default, not a card-resource switch.
  return row ? cardConfiguration(JSON.parse(row.config_json).card)[environment] ?? null : null;
}

export function legacyRecoveryHandbookConfiguration(raw: string, target: RecoveryRequest['target']) {
  const legacy = parseLegacyRecoveryHandbooks(raw);
  return { teamKey: legacy.bot_teams.find(item => item.env === target.env && item.bot_id === target.bot_id
    && item.owner_id === target.entity_id)?.team_key, routes: legacy.team_routes };
}
