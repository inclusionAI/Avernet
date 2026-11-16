import type { BotDiagnosisRecoveryConfigStore } from '../../monitoring/bot-config-contracts.js';
import { parseBotNotifications } from '../../monitoring/bot-config-contracts.js';
import type { RecoveryRequest } from './contracts.js';
import { SessionRecoveryError } from './errors.js';

/** Recovery entity_id is the Owner, while configuration uses the directory entity. */
export function recoveryConfigOwner(target: RecoveryRequest['target']) {
  return { botId: target.bot_id, ownerId: target.entity_id, env: target.env };
}
export async function requireRecoveryEnabled(store: BotDiagnosisRecoveryConfigStore, request: RecoveryRequest) {
  let config;
  try { config = await store.findForOwner(recoveryConfigOwner(request.target)); }
  catch { throw new SessionRecoveryError(503, 'recovery_configuration_unavailable', '自愈配置暂不可用'); }
  if (!config?.recoveryEnabled) throw new SessionRecoveryError(403, 'recovery_disabled', '此 Bot 尚未开启自愈');
}
export async function recoveryNotifications(store: BotDiagnosisRecoveryConfigStore | undefined, target: RecoveryRequest['target']) {
  try { return parseBotNotifications((await store?.findForOwner(recoveryConfigOwner(target)))?.configJson ?? '{}'); }
  catch { return { owner: false, group: null }; }
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
