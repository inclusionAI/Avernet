import type { BotDiagnosisRecoveryConfigStore } from '../../monitoring/bot-config-contracts.js';
import { parseBotNotifications } from '../../monitoring/bot-config-contracts.js';
import type { MonitoringTarget } from '../../monitoring/contracts.js';
import { targetKey } from '../../monitoring/target.js';
import type { RecoveryRequest } from './contracts.js';
import { SessionRecoveryError } from './errors.js';
import {
  type RecoveryAppConfigStore, legacyOwnerNotificationEnabled, legacyWebhookConfig,
  parseLegacyRecoveryAllowlist, SESSION_RECOVERY_ALLOWLIST_KEY,
  SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY, SESSION_RECOVERY_NOTIFICATIONS_KEY,
} from './legacy-configuration.js';

/** Recovery entity_id is the Owner, while configuration uses the directory entity. */
export function recoveryConfigOwner(target: RecoveryRequest['target']) {
  return { botId: target.bot_id, ownerId: target.entity_id, env: target.env };
}

async function legacyAllowedTargets(app?: RecoveryAppConfigStore): Promise<Set<string>> {
  const row = await app?.findByKey(SESSION_RECOVERY_ALLOWLIST_KEY);
  return new Set(row?.enabled === 1 ? parseLegacyRecoveryAllowlist(JSON.parse(row.config_json)).map(targetKey) : []);
}

/** One source-selection boundary: only an absent row may use the read-only legacy policy. */
export async function recoveryConfigurations(store: BotDiagnosisRecoveryConfigStore, targets: readonly MonitoringTarget[], app?: RecoveryAppConfigStore) {
  const rows = await store.findMany(targets);
  const allowed = rows.some(row => row === null) ? await legacyAllowedTargets(app) : new Set<string>();
  return targets.map((target, index) => rows[index] ?? { ...target, diagnosisEnabled: false,
    recoveryEnabled: allowed.has(targetKey(target)), configJson: '{}', version: 0 });
}

async function configurationForOwner(store: BotDiagnosisRecoveryConfigStore, target: RecoveryRequest['target']) {
  const directoryTarget = await store.resolveTargetForOwner(recoveryConfigOwner(target));
  if (!directoryTarget) return null;
  return { target: directoryTarget, config: await store.find(directoryTarget) };
}

export async function requireRecoveryEnabled(store: BotDiagnosisRecoveryConfigStore, request: RecoveryRequest, app?: RecoveryAppConfigStore) {
  let enabled = false;
  try {
    const resolved = await configurationForOwner(store, request.target);
    if (resolved) enabled = resolved.config ? resolved.config.recoveryEnabled
      : (await legacyAllowedTargets(app)).has(targetKey(resolved.target));
  } catch { throw new SessionRecoveryError(503, 'recovery_configuration_unavailable', '自愈配置暂不可用'); }
  if (!enabled) throw new SessionRecoveryError(403, 'recovery_disabled', '此 Bot 尚未开启自愈');
}

type RecoveryNotifications = { owner: boolean; group: { webhook: string; timeout_seconds: number } | null };
export async function recoveryNotifications(store: BotDiagnosisRecoveryConfigStore | undefined, target: RecoveryRequest['target'], app?: RecoveryAppConfigStore): Promise<RecoveryNotifications> {
  const disabled = { owner: false, group: null };
  try {
    if (!store) return disabled;
    const resolved = await configurationForOwner(store, target);
    if (!resolved) return disabled;
    if (resolved.config) {
      const notifications = parseBotNotifications(resolved.config.configJson);
      if (!notifications.group) return { owner: notifications.owner, group: null };
      const row = await app?.findByKey(RECOVERY_NOTIFICATION_ROUTES_KEY);
      const group = row?.enabled === 1 ? recoveryGroupRoute(row.config_json, notifications.group.routeId) : null;
      return { owner: notifications.owner, group };
    }
    // In-flight legacy tasks keep their notification policy even after admission is disabled.
    // No fallback is permitted for malformed new JSON, disabled switches or storage errors.
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
  } catch { return disabled; }
}

export const RECOVERY_NOTIFICATION_ROUTES_KEY = 'session_recovery_notification_routes';
export function recoveryGroupRoute(raw: string, routeId: string): { webhook: string; timeout_seconds: number } | null {
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value) || !value.routes || typeof value.routes !== 'object' || Array.isArray(value.routes)) return null;
    if (!Object.hasOwn(value.routes, routeId)) return null;
    const route = value.routes[routeId];
    if (!route || typeof route !== 'object' || Array.isArray(route) || typeof route.webhook !== 'string'
      || !Number.isInteger(route.timeout_seconds) || route.timeout_seconds < 1 || route.timeout_seconds > 60) return null;
    const url = new URL(route.webhook);
    if (url.protocol !== 'https:' || url.username || url.password) return null;
    return { webhook: route.webhook, timeout_seconds: route.timeout_seconds };
  } catch { return null; }
}
