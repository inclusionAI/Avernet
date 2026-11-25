import { createHash } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { AppConfigRepository } from '@avernet/clawevolve/server/repositories/app-config-repository';
import { BotDiagnosisRecoveryConfigRepository } from '../../repositories/bot-diagnosis-recovery-config-repository.js';
import type { DirectoryScope, MonitoringBotDirectory } from './directory-contracts.js';
import type { MonitoringTarget } from './contracts.js';
import { targetKey } from './target.js';
import { parseLegacyRecoveryAllowlist, SESSION_RECOVERY_ALLOWLIST_KEY } from '../repair/session-recovery/legacy-configuration.js';
import { legacyOwnerNotificationEnabled, legacyWebhookConfig, SESSION_RECOVERY_NOTIFICATIONS_KEY, SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY } from '../repair/session-recovery/legacy-configuration.js';
import { RECOVERY_NOTIFICATION_ROUTES_KEY, recoveryGroupRoute } from '../repair/session-recovery/bot-configuration.js';

/** Explicit administrative migration, never run at startup. Dry-run is the default; reports contain no webhook URLs. */
export async function migrateBotDiagnosisRecoveryConfigs(db: IDatabase, directory: MonitoringBotDirectory, scope: DirectoryScope,
  options: { apply?: boolean; updatedBy: string }) {
  const app = new AppConfigRepository(db), configurations = new BotDiagnosisRecoveryConfigRepository(db);
  const keys = [SESSION_RECOVERY_ALLOWLIST_KEY, SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY, SESSION_RECOVERY_NOTIFICATIONS_KEY, RECOVERY_NOTIFICATION_ROUTES_KEY];
  const rows = await Promise.all(keys.map(key => app.findByKey(key)));
  const [allow, owner, group, currentRoutes] = rows;
  const warnings: string[] = [];
  let allowed: MonitoringTarget[] = [];
  try { if (allow?.enabled === 1) allowed = parseLegacyRecoveryAllowlist(JSON.parse(allow.config_json)); }
  catch { warnings.push('旧白名单无效，未迁移自愈启用状态'); }
  let legacyGroup: ReturnType<typeof legacyWebhookConfig> | null = null;
  try { if (group?.enabled === 1) legacyGroup = legacyWebhookConfig(group.config_json); }
  catch { warnings.push('旧群通知配置无效，群通知保持关闭'); }
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
  for (const t of allowed) {
    const bot = await directory.get(t, scope);
    if (!bot) { warnings.push(`目录目标不存在：${targetKey(t)}`); continue; }
    if (await configurations.find(t)) { skipped++; continue; }
    let ownerEnabled = false;
    try { if (owner && [0, 1].includes(owner.enabled)) ownerEnabled = legacyOwnerNotificationEnabled(owner.config_json, owner.enabled,
      { botId: bot.botId, ownerId: bot.ownerId, targetEnvironment: bot.env as 'pre' | 'prod' }); }
    catch { warnings.push(`个人通知配置无效：${targetKey(t)}`); }
    const webhook = legacyGroup?.bot_webhooks?.find(item => item.bot_id === bot.botId && item.entity_id === bot.ownerId)?.webhook ?? legacyGroup?.webhook;
    const routeId = webhook && legacyGroup ? route(webhook, legacyGroup.timeout_seconds) : null;
    planned.push({ ...t, diagnosisEnabled: false, recoveryEnabled: true,
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
