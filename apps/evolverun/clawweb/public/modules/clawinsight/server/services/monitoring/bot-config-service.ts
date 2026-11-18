import { validateRecoveryTeamConfiguration } from "../repair/session-recovery/bot-configuration.js";
import type { RecoveryAppConfigStore } from "../repair/session-recovery/configuration-contracts.js";
import { MonitoringError } from './contracts.js';
import { validBotConfiguration, type BotDiagnosisRecoveryConfigStore } from './bot-config-contracts.js';
import type { DirectoryBot, MonitoringBotDirectory, MonitoringPrincipal, MonitoringReferences } from './directory-contracts.js';

export function isServiceBot(bot: DirectoryBot): boolean {
  return bot.botType?.trim().toLowerCase() === 'service';
}

export async function replaceBotConfiguration(store: BotDiagnosisRecoveryConfigStore, bot: DirectoryBot, updatedBy: string, input: unknown, app?: RecoveryAppConfigStore) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new MonitoringError('INVALID_EVENT', '配置参数无效。');
  const value = input as Record<string, unknown>;
  const { diagnosisEnabled, recoveryEnabled, version, configJson } = value;
  if (Object.keys(value).some(key => !['diagnosisEnabled', 'recoveryEnabled', 'version', 'configJson'].includes(key))
    || typeof diagnosisEnabled !== 'boolean' || typeof recoveryEnabled !== 'boolean'
    || typeof version !== 'number' || !Number.isSafeInteger(version) || version < 0
    || typeof configJson !== 'string' || Buffer.byteLength(configJson) > 8192 || !validBotConfiguration(configJson)) {
    throw new MonitoringError('INVALID_EVENT', '配置参数或通知配置无效。');
  }
  try { await validateRecoveryTeamConfiguration(configJson, app); }
  catch { throw new MonitoringError('NOT_READY', '诊断自愈团队配置暂不可用，请检查团队配置。'); }
  return store.replaceConfiguration(bot, { diagnosisEnabled, recoveryEnabled, configJson, expectedVersion: version, updatedBy });
}

/** Only current owners may write, including when the caller can read all Bots. */
export async function configureBots(store: BotDiagnosisRecoveryConfigStore | undefined, directory: MonitoringBotDirectory,
  references: MonitoringReferences, principal: MonitoringPrincipal, input: unknown) {
  if (!store) throw new MonitoringError('NOT_READY', '诊断监控配置尚未就绪。');
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new MonitoringError('INVALID_EVENT', '配置参数无效。');
  const value = input as Record<string, unknown>;
  const all = value.all === true;
  if (typeof value.enabled !== 'boolean' || Object.keys(value).some(k => !(all ? ['all', 'enabled'] : ['botRef', 'enabled', 'version']).includes(k))
    || !all && (typeof value.botRef !== 'string' || !Number.isSafeInteger(value.version) || Number(value.version) < 0)) {
    throw new MonitoringError('INVALID_EVENT', '配置参数无效。');
  }
  let updated = 0;
  const failed: string[] = [];
  const write = async (bot: import('./directory-contracts.js').DirectoryBot, version: number) => {
    if (bot.ownerId !== principal.staffId) throw new MonitoringError('FORBIDDEN', '仅 Bot 所有者可以修改配置。');
    if (bot.env !== 'prod') throw new MonitoringError('FORBIDDEN', '仅支持生产环境 Bot 的诊断自愈配置。');
    if (isServiceBot(bot)) throw new MonitoringError('FORBIDDEN', '服务 Bot 请联系平台设置诊断自愈。');
    const switches = { diagnosisEnabled: value.enabled as boolean, recoveryEnabled: value.enabled as boolean,
      expectedVersion: version, updatedBy: principal.staffId };
    // Initialize only a new row. Existing settings remain administrator-owned, including historical empty JSON.
    if (version === 0) await store.replaceConfiguration(bot, { ...switches, configJson: JSON.stringify({
      team_key: 'default', notifications: { owner: { enabled: true }, group: { enabled: true } },
    }) });
    else await store.setSwitches(bot, switches);
    updated++;
  };
  if (!all) {
    const bot = await directory.get(references.decode(value.botRef as string, principal.tenant), principal);
    if (!bot) throw new MonitoringError('BOT_NOT_FOUND', 'Bot 不存在或无权访问。');
    await write(bot, Number(value.version));
  } else {
    if (!principal.allowedTargetEnvs.includes('prod')) return { updated, failed };
    const productionPrincipal = { ...principal, allowedTargetEnvs: ['prod'] };
    // Keyset traversal freezes the current roster's upper bound at the first page.
    let after: string | null = null;
    for (;;) {
      const page = await directory.search({ principal: productionPrincipal, scope: 'mine', q: '', limit: 50, after });
      const configs = await store.findMany(page.items);
      for (let i = 0; i < page.items.length; i++) {
        const bot = page.items[i];
        // Revalidate ownership immediately before mutation, never trust an old UI roster.
        const current = await directory.get(bot, principal);
        if (!current || current.ownerId !== principal.staffId) { failed.push(references.encode(bot, principal.tenant)); continue; }
        if (current.env !== 'prod' || isServiceBot(current)) continue;
        try { await write(current, configs[i]?.version ?? 0); }
        catch { failed.push(references.encode(bot, principal.tenant)); }
      }
      if (!page.hasMore || !page.items.length) break;
      after = page.items[page.items.length - 1].directoryId;
    }
  }
  return { updated, failed };
}
