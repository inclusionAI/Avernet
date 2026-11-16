import { createHash } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { AppConfigRepository } from '@avernet/clawevolve/server/repositories/app-config-repository';
import { MonitoringRepository } from '../../repositories/monitoring-repository.js';
import { BotDiagnosisRecoveryConfigRepository } from '../../repositories/bot-diagnosis-recovery-config-repository.js';
import type { DirectoryBot, DirectoryScope, MonitoringBotDirectory } from './directory-contracts.js';
import type { MonitoringTarget } from './contracts.js';
import { targetKey } from './target.js';
import { parseLegacyRecoveryAllowlist, SESSION_RECOVERY_ALLOWLIST_KEY } from './session-recovery-trigger.js';
import { legacyOwnerNotificationEnabled, legacyWebhookConfig, SESSION_RECOVERY_NOTIFICATIONS_KEY, SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY } from '../repair/session-recovery/notification.js';
import { RECOVERY_NOTIFICATION_ROUTES_KEY, recoveryGroupRoute } from '../repair/session-recovery/bot-configuration.js';

/** Explicit administrative migration, never run at startup. Dry-run is the default; reports contain no webhook URLs. */
export async function migrateBotDiagnosisRecoveryConfigs(db: IDatabase, directory: MonitoringBotDirectory, scope: DirectoryScope,
  options: { apply?: boolean; updatedBy: string }) {
  const app = new AppConfigRepository(db), configurations = new BotDiagnosisRecoveryConfigRepository(db);
  const keys = [SESSION_RECOVERY_ALLOWLIST_KEY, SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY, SESSION_RECOVERY_NOTIFICATIONS_KEY, RECOVERY_NOTIFICATION_ROUTES_KEY];
  const rows = await Promise.all(keys.map(key => app.findByKey(key)));
  const [allow, owner, group, currentRoutes] = rows;
  const warnings: string[] = [], candidates = new Map<string, MonitoringTarget>();
  const checked = await new MonitoringRepository(db).listCheckedTargets();
  const monitored = new Set(checked.map(targetKey));
  const add = (t: MonitoringTarget) => candidates.set(targetKey(t), t);
  checked.forEach(add);
  let allowed: MonitoringTarget[] = [];
  try { if (allow?.enabled === 1) allowed = parseLegacyRecoveryAllowlist(JSON.parse(allow.config_json)); }
  catch { warnings.push('旧白名单无效，未迁移自愈启用状态'); }
  allowed.forEach(add);
  const enabled = new Set(allowed.map(targetKey));
  const ownerTargets = async (botId: string, ownerId: string, env?: string) => {
    let after: string | null = null;
    const matches: DirectoryBot[] = [];
    for (;;) {
      const page = await directory.search({ principal: { ...scope, staffId: ownerId, isAuthenticated: true, isClawInsightAdmin: false }, scope: 'mine', q: botId, limit: 50, after });
      matches.push(...page.items.filter(bot => bot.botId === botId && bot.ownerId === ownerId && (!env || bot.env === env)));
      if (!page.hasMore || !page.items.length) break;
      after = page.items[page.items.length - 1].directoryId;
    }
    matches.forEach(add);
    if (!matches.length) warnings.push(`目录目标无法解析：${botId}/${ownerId}/${env ?? '*'}`);
  };
  let legacyGroup: ReturnType<typeof legacyWebhookConfig> | null = null;
  try { if (group?.enabled === 1) legacyGroup = legacyWebhookConfig(group.config_json); }
  catch { warnings.push('旧群通知配置无效，群通知保持关闭'); }
  for (const item of legacyGroup?.bot_webhooks ?? []) await ownerTargets(item.bot_id, item.entity_id);
  try {
    const overrides = owner ? JSON.parse(owner.config_json).bot_overrides ?? [] : [];
    if (!Array.isArray(overrides)) throw new Error('Invalid overrides');
    for (const item of overrides) {
      if (typeof item.bot_id !== 'string' || typeof item.entity_id !== 'string' || typeof item.env !== 'string') throw new Error('Invalid identity');
      await ownerTargets(item.bot_id, item.entity_id, item.env);
    }
  } catch { warnings.push('旧个人通知目标无效，无法解析的目标不迁移'); }
  // Include historical/in-flight task targets so later notification work has an explicit policy too.
  let afterTaskId = '';
  for (;;) {
    const tasks = await db.query<{ task_id: string; config_json: string }>("SELECT task_id, config_json FROM ce_tasks WHERE task_type = 'session_recovery' AND task_id > ? ORDER BY task_id LIMIT 100", [afterTaskId]);
    for (const task of tasks) {
      try { const t = JSON.parse(task.config_json).request.target; await ownerTargets(t.bot_id, t.entity_id, t.env); }
      catch { warnings.push(`历史任务身份无法解析：${task.task_id}`); }
    }
    if (tasks.length < 100) break;
    afterTaskId = tasks[tasks.length - 1].task_id;
  }
  const routes: Record<string, { name?: string; webhook: string; timeout_seconds: number }> = currentRoutes ? JSON.parse(currentRoutes.config_json).routes : {};
  if (!routes || typeof routes !== 'object' || Array.isArray(routes)) throw new Error('Existing group routes are invalid; migration stopped');
  const route = (webhook: string, timeout: number) => {
    const id = `legacy_${createHash('sha256').update(JSON.stringify([webhook, timeout])).digest('hex').slice(0, 20)}`;
    const old = routes[id];
    if (old && (old.webhook !== webhook || old.timeout_seconds !== timeout)) throw new Error('Group route identity conflict');
    routes[id] = old ?? { name: '迁移群路由', webhook, timeout_seconds: timeout };
    if (!recoveryGroupRoute(JSON.stringify({ routes }), id)) throw new Error('Invalid legacy group route');
    return id;
  };
  const planned: Array<MonitoringTarget & { diagnosisEnabled: boolean; recoveryEnabled: boolean; configJson: string }> = [];
  let skipped = 0;
  for (const t of candidates.values()) {
    const bot = await directory.get(t, scope);
    if (!bot) { warnings.push(`目录目标不存在：${targetKey(t)}`); continue; }
    if (await configurations.find(t)) { skipped++; continue; }
    let ownerEnabled = false;
    try { if (owner && [0, 1].includes(owner.enabled)) ownerEnabled = legacyOwnerNotificationEnabled(owner.config_json, owner.enabled,
      { botId: bot.botId, ownerId: bot.ownerId, targetEnvironment: bot.env as 'pre' | 'prod', taskId: '', sessionKey: '', kind: 'plan', summary: '' }); }
    catch { warnings.push(`个人通知配置无效：${targetKey(t)}`); }
    const webhook = legacyGroup?.bot_webhooks?.find(item => item.bot_id === bot.botId && item.entity_id === bot.ownerId)?.webhook ?? legacyGroup?.webhook;
    const routeId = webhook && legacyGroup ? route(webhook, legacyGroup.timeout_seconds) : null;
    planned.push({ ...t, diagnosisEnabled: monitored.has(targetKey(t)), recoveryEnabled: enabled.has(targetKey(t)),
      configJson: JSON.stringify({ notifications: { owner: { enabled: ownerEnabled }, group: routeId ? { enabled: true, route_id: routeId } : { enabled: false } } }) });
  }
  if (options.apply) await db.transaction(async tx => {
    // Abort on a source edit between planning and writing. No legacy values are rewritten.
    const current = new AppConfigRepository(tx);
    for (let i = 0; i < keys.length; i++) {
      const [row] = await tx.query<{ version: number; config_json: string; enabled: number }>(
        `SELECT version, config_json, enabled FROM ce_app_config WHERE config_key = ?${tx.dbType === 'sqlite' ? '' : ' FOR UPDATE'}`, [keys[i]]);
      if (row?.version !== rows[i]?.version || row?.config_json !== rows[i]?.config_json || row?.enabled !== rows[i]?.enabled) throw new Error('Configuration changed; rerun migration dry-run');
    }
    const registry = JSON.stringify({ routes });
    if (Object.keys(routes).length) {
      if (currentRoutes && currentRoutes.config_json !== registry) await current.update(RECOVERY_NOTIFICATION_ROUTES_KEY, { config_json: registry, updated_by: options.updatedBy });
      else if (!currentRoutes) await current.create({ config_key: RECOVERY_NOTIFICATION_ROUTES_KEY, config_json: registry, updated_by: options.updatedBy });
    }
    const config = new BotDiagnosisRecoveryConfigRepository(tx);
    for (const item of planned) {
      if (await config.find(item)) { skipped++; continue; }
      const now = tx.dialect.now();
      await tx.exec(`INSERT INTO ce_bot_diagnosis_recovery_config
        (bot_id, entity_id, env, diagnosis_enabled, recovery_enabled, config_json, version, updated_by, gmt_create, gmt_modified)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)`, [item.botId, item.entityId, item.env, Number(item.diagnosisEnabled), Number(item.recoveryEnabled), item.configJson, options.updatedBy, now, now]);
    }
  });
  return { applied: options.apply === true, planned, skipped, routeCount: Object.keys(routes).length, warnings };
}
