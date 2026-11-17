import { parseRecoveryHandbookRoutes, recoveryHandbookForTeam, type RecoveryHandbookConfigStore, SESSION_RECOVERY_HANDBOOKS_KEY } from "./handbook.js";
import { parseLegacyRecoveryHandbooks } from "./legacy-configuration.js";
import type { RecoveryStoredTask } from "./task-contracts.js";
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
      const teamKey = botTeamKey(resolved.config.configJson);
      const routeId = notifications.group.routeId ?? (teamKey ? (await recoveryTeam(teamKey, app)).notification_route_id : undefined);
      if (!routeId) return { owner: notifications.owner, group: null };
      const row = await app?.findByKey(RECOVERY_NOTIFICATION_ROUTES_KEY);
      const group = row?.enabled === 1 ? recoveryGroupRoute(row.config_json, routeId) : null;
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

export const SESSION_RECOVERY_TEAMS_KEY = 'session_recovery_teams';
type RecoveryTeam = { name?: string; notification_route_id?: string; llm?: { api_key_mist_key?: string } };
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);

function configurationInvalid(): never {
  throw new SessionRecoveryError(503, 'recovery_configuration_invalid', '诊断自愈团队配置无效');
}
function botTeamKey(raw: string): string | undefined {
  let value: unknown;
  try { value = JSON.parse(raw); } catch { return configurationInvalid(); }
  if (!object(value)) return configurationInvalid();
  if (value.team_key === undefined) return undefined;
  if (typeof value.team_key !== 'string' || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value.team_key)) return configurationInvalid();
  return value.team_key;
}
async function recoveryTeam(key: string, app?: RecoveryAppConfigStore): Promise<RecoveryTeam> {
  const row = await app?.findByKey(SESSION_RECOVERY_TEAMS_KEY);
  // The default team can be used before any optional team resources are configured.
  if (!row || row.enabled === 0) {
    if (key === 'default') return {};
    return configurationInvalid();
  }
  if (row.enabled !== 1) return configurationInvalid();
  let value: unknown;
  try { value = JSON.parse(row.config_json); } catch { return configurationInvalid(); }
  if (!object(value) || !object(value.teams)) return configurationInvalid();
  if (!Object.hasOwn(value.teams, key)) {
    if (key === 'default') return {};
    return configurationInvalid();
  }
  const team = value.teams[key];
  if (!object(team) || Object.keys(team).some(field => !['name', 'notification_route_id', 'llm'].includes(field))
    || team.name !== undefined && typeof team.name !== 'string'
    || team.notification_route_id !== undefined && (typeof team.notification_route_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(team.notification_route_id))) return configurationInvalid();
  if (team.llm !== undefined && (!object(team.llm) || Object.keys(team.llm).some(field => field !== 'api_key_mist_key')
    || team.llm.api_key_mist_key !== undefined && (typeof team.llm.api_key_mist_key !== 'string'
      || !/^[A-Za-z0-9_.-]{1,256}$/.test(team.llm.api_key_mist_key)))) return configurationInvalid();
  return team as RecoveryTeam;
}

export async function validateRecoveryTeamConfiguration(raw: string, app?: RecoveryAppConfigStore): Promise<void> {
  const key = botTeamKey(raw);
  if (key) await recoveryTeam(key, app);
}

/** Resolve legacy ownership and current team policy here, once at creation. Consumers see only effective values. */
export async function recoveryExecutionConfiguration(request: RecoveryRequest, app?: RecoveryHandbookConfigStore,
  store?: BotDiagnosisRecoveryConfigStore) {
  const resolved = store ? await configurationForOwner(store, request.target) : null;
  let teamKey = resolved?.config ? botTeamKey(resolved.config.configJson) : undefined;
  const team = teamKey ? await recoveryTeam(teamKey, app) : undefined;
  const row = await app?.findByKey(SESSION_RECOVERY_HANDBOOKS_KEY);
  let routes: Record<string, Record<string, string[]>> = {};
  if (row && row.enabled !== 0) {
    if (row.enabled !== 1) return configurationInvalid();
    if (teamKey === undefined) {
      const legacy = parseLegacyRecoveryHandbooks(row.config_json);
      teamKey = legacy.bot_teams.find(item => item.env === request.target.env && item.bot_id === request.target.bot_id
        && item.owner_id === request.target.entity_id)?.team_key;
      routes = legacy.team_routes;
    } else {
      // New ownership is independent of the obsolete Bot roster, including its validation.
      let value: unknown;
      try { value = JSON.parse(row.config_json); } catch { return configurationInvalid(); }
      if (!object(value)) return configurationInvalid();
      routes = parseRecoveryHandbookRoutes(value.team_routes);
    }
  }
  // A team without handbook resources uses common; the wrapper must not require a nonexistent custom directory.
  const handbookTeam = teamKey && Object.hasOwn(routes, teamKey) ? teamKey : null;
  return { handbook: recoveryHandbookForTeam(request.tc_fault_label, handbookTeam, routes),
    modelApiKeyRef: team?.llm?.api_key_mist_key ?? null };
}

/** Existing callers can keep requesting just the handbook through the same configuration boundary. */
export async function resolveRecoveryHandbook(request: RecoveryRequest, app: RecoveryHandbookConfigStore) {
  return (await recoveryExecutionConfiguration(request, app)).handbook;
}

/** Old task snapshots select the deployment default; never re-resolve a running task's Bot team. */
export async function recoveryModelCredentials(task: RecoveryStoredTask, resolve?: (reference: string) => Promise<string>): Promise<{ modelApiKey?: string }> {
  if (!task.modelApiKeyRef) return {};
  try {
    if (!resolve) throw new Error('Model credential resolver unavailable');
    const value = await resolve(task.modelApiKeyRef);
    if (typeof value !== 'string' || !value.trim() || value.length > 8192 || /[\r\n\0]/.test(value)) throw new Error('Invalid model credential');
    return { modelApiKey: value };
  } catch {
    throw new SessionRecoveryError(503, 'recovery_model_credentials_unavailable', '自愈模型凭据暂不可用');
  }
}
