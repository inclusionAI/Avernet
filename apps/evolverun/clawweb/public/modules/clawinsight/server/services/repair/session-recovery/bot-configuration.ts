import { logRepairEvent } from '../common/logging.js';
import { parseRecoveryHandbookRoutes, recoveryHandbookForTeam, type RecoveryHandbookConfigStore, SESSION_RECOVERY_HANDBOOKS_KEY } from "./handbook.js";
import type { RecoveryStoredTask } from "./task-contracts.js";
import type { BotDiagnosisRecoveryConfigStore } from '../../monitoring/bot-config-contracts.js';
import { parseBotNotifications, validBotConfiguration } from '../../monitoring/bot-config-contracts.js';
import type { MonitoringTarget } from '../../monitoring/contracts.js';
import { targetKey } from '../../monitoring/target.js';
import type { RecoveryRequest } from './contracts.js';
import { SessionRecoveryError } from './errors.js';
import { cardConfiguration, type RecoveryAppConfigStore, type RecoveryNotifications, type RecoveryCardConfiguration } from './configuration-contracts.js';
import type { AgentControlPlaneEnvironment } from '../common/task-control-plane.js';
import { legacyAllowedTargets, legacyRecoveryNotifications, legacyRecoveryCardConfiguration, legacyRecoveryHandbookConfiguration } from './legacy-configuration.js';

/** Recovery entity_id is the Owner, while configuration uses the directory entity. */
export function recoveryConfigOwner(target: RecoveryRequest['target']) {
  return { botId: target.bot_id, ownerId: target.entity_id, env: target.env };
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
  let source = 'unresolved';
  const fields = { configuration: 'admission', eventId: request.event_id, botId: request.target.bot_id,
    ownerId: request.target.entity_id, targetEnvironment: request.target.env };
  try {
    const resolved = await configurationForOwner(store, request.target);
    source = !resolved ? 'none' : resolved.config ? 'new' : 'legacy';
    if (resolved) enabled = resolved.config ? resolved.config.recoveryEnabled
      : (await legacyAllowedTargets(app)).has(targetKey(resolved.target));
  } catch {
    logRepairEvent('session-recovery', 'session_recovery_configuration', 'resolution', { ...fields, source, status: 'unavailable', reason: 'read_or_parse_failed' });
    throw new SessionRecoveryError(503, 'recovery_configuration_unavailable', '自愈配置暂不可用');
  }
  logRepairEvent('session-recovery', 'session_recovery_configuration', 'resolution', { ...fields, source, enabled,
    status: enabled ? 'enabled' : 'disabled' });
  if (!enabled) throw new SessionRecoveryError(403, 'recovery_disabled', '此 Bot 尚未开启自愈');
}

export async function recoveryNotifications(store: BotDiagnosisRecoveryConfigStore | undefined, target: RecoveryRequest['target'], app?: RecoveryAppConfigStore,
  taskId?: string): Promise<RecoveryNotifications> {
  const disabled = { owner: false, group: null };
  let source = 'unresolved', routeSource = 'none';
  let teamKey: string | undefined;
  const finish = (value: RecoveryNotifications, reason: string) => {
    logRepairEvent('session-recovery', 'session_recovery_configuration', 'resolution', { configuration: 'notifications', source,
      botId: target.bot_id, ownerId: target.entity_id, targetEnvironment: target.env, taskId, teamKey, routeSource,
      ownerEnabled: value.owner, groupEnabled: value.group !== null, reason });
    return value;
  };
  try {
    if (!store) { source = 'none'; return finish(disabled, 'store_unavailable'); }
    const resolved = await configurationForOwner(store, target);
    if (!resolved) { source = 'none'; return finish(disabled, 'target_unresolved'); }
    source = resolved.config ? 'new' : 'legacy';
    if (resolved.config) {
      if (!validBotConfiguration(resolved.config.configJson)) return finish(disabled, 'invalid_configuration');
      const notifications = parseBotNotifications(resolved.config.configJson);
      if (!notifications.group) return finish({ owner: notifications.owner, group: null }, 'group_disabled_or_unconfigured');
      teamKey = botTeamKey(resolved.config.configJson);
      routeSource = notifications.group.routeId ? 'bot' : teamKey ? 'team' : 'none';
      const routeId = notifications.group.routeId ?? (teamKey ? (await recoveryTeam(teamKey, app)).notification_route_id : undefined);
      if (!routeId) return finish({ owner: notifications.owner, group: null }, 'route_unconfigured');
      const row = await app?.findByKey(RECOVERY_NOTIFICATION_ROUTES_KEY);
      const group = row?.enabled === 1 ? recoveryGroupRoute(row.config_json, routeId) : null;
      return finish({ owner: notifications.owner, group }, group ? 'resolved' : 'route_unavailable');
    }
    // In-flight legacy tasks keep their notification policy even after admission is disabled.
    // No fallback is permitted for malformed new JSON, disabled switches or storage errors.
    routeSource = 'legacy';
    return finish(await legacyRecoveryNotifications(target, app), 'resolved');
  } catch { return finish(disabled, 'read_or_parse_failed'); }
}

export const SESSION_RECOVERY_CARDS_KEY = 'session_recovery_cards';
/** Only an absent new row permits compatibility; every invalid/unavailable value fails closed. */
export async function recoveryCardConfiguration(app: RecoveryAppConfigStore, environment: AgentControlPlaneEnvironment,
  context: { taskId?: string; botId?: string; ownerId?: string; targetEnvironment?: string } = {}): Promise<RecoveryCardConfiguration | null> {
  let source = 'unresolved';
  const finish = (value: RecoveryCardConfiguration | null, reason: string) => {
    logRepairEvent('session-recovery', 'session_recovery_configuration', 'resolution', { ...context, environment,
      configuration: 'cards', source, enabled: value !== null, reason });
    return value;
  };
  try {
    const row = await app.findByKey(SESSION_RECOVERY_CARDS_KEY);
    source = row ? 'new' : 'legacy';
    if (!row) {
      const legacy = await legacyRecoveryCardConfiguration(app, environment);
      return finish(legacy, legacy ? 'resolved' : 'legacy_disabled_or_unconfigured');
    }
    if (row.enabled !== 1) return finish(null, 'disabled');
    const card = cardConfiguration(JSON.parse(row.config_json))[environment] ?? null;
    return finish(card, card ? 'resolved' : 'environment_unconfigured');
  } catch { return finish(null, 'read_or_parse_failed'); }
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
  let source = 'unresolved';
  const fields = { configuration: 'execution', eventId: request.event_id, botId: request.target.bot_id,
    ownerId: request.target.entity_id, targetEnvironment: request.target.env };
  try {
    const resolved = store ? await configurationForOwner(store, request.target) : null;
    source = resolved?.config ? 'new' : 'legacy';
    let teamKey = resolved?.config ? botTeamKey(resolved.config.configJson) : undefined;
    source = teamKey === undefined ? 'legacy' : 'new';
    const team = teamKey ? await recoveryTeam(teamKey, app) : undefined;
    const row = await app?.findByKey(SESSION_RECOVERY_HANDBOOKS_KEY);
    let routes: Record<string, Record<string, string[]>> = {};
    if (row && row.enabled !== 0) {
      if (row.enabled !== 1) return configurationInvalid();
      if (teamKey === undefined) {
        ({ teamKey, routes } = legacyRecoveryHandbookConfiguration(row.config_json, request.target));
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
    logRepairEvent('session-recovery', 'session_recovery_configuration', 'resolution', { ...fields, source, teamKey,
      status: 'resolved', reason: team?.llm?.api_key_mist_key ? 'team_model_credentials' : 'default_model_credentials' });
    return { handbook: recoveryHandbookForTeam(request.tc_fault_label, handbookTeam, routes),
      modelApiKeyRef: team?.llm?.api_key_mist_key ?? null };
  } catch (error) {
    logRepairEvent('session-recovery', 'session_recovery_configuration', 'resolution', { ...fields, source, status: 'unavailable', reason: 'read_or_parse_failed' });
    throw error;
  }
}

/** Existing callers can keep requesting just the handbook through the same configuration boundary. */
export async function resolveRecoveryHandbook(request: RecoveryRequest, app: RecoveryHandbookConfigStore) {
  return (await recoveryExecutionConfiguration(request, app)).handbook;
}

/** Old task snapshots select the deployment default; never re-resolve a running task's Bot team. */
export async function recoveryModelCredentials(task: RecoveryStoredTask, resolve?: (reference: string) => Promise<string>): Promise<{ modelApiKey?: string }> {
  const fields = { configuration: 'model_credentials', taskId: task.taskId, botId: task.request.target.bot_id,
    ownerId: task.request.target.entity_id, targetEnvironment: task.request.target.env };
  if (!task.modelApiKeyRef) {
    logRepairEvent('session-recovery', 'session_recovery_configuration', 'resolution', { ...fields, source: 'default', status: 'resolved' });
    return {};
  }
  try {
    if (!resolve) throw new Error('Model credential resolver unavailable');
    const value = await resolve(task.modelApiKeyRef);
    if (typeof value !== 'string' || !value.trim() || value.length > 8192 || /[\r\n\0]/.test(value)) throw new Error('Invalid model credential');
    logRepairEvent('session-recovery', 'session_recovery_configuration', 'resolution', { ...fields, source: 'mist',
      mistKey: task.modelApiKeyRef, status: 'resolved' });
    return { modelApiKey: value };
  } catch {
    logRepairEvent('session-recovery', 'session_recovery_configuration', 'resolution', { ...fields, source: 'mist',
      mistKey: task.modelApiKeyRef, status: 'unavailable', reason: 'credential_resolution_failed' });
    throw new SessionRecoveryError(503, 'recovery_model_credentials_unavailable', '自愈模型凭据暂不可用');
  }
}
