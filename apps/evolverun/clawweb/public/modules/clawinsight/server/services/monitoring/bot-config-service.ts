import { validateRecoveryTeamConfiguration } from "../repair/session-recovery/bot-configuration.js";
import type { RecoveryAppConfigStore } from "../repair/session-recovery/configuration-contracts.js";
import { MonitoringError } from './contracts.js';
import { validBotConfiguration, type BotDiagnosisRecoveryConfig, type BotDiagnosisRecoveryConfigStore } from './bot-config-contracts.js';
import type { DirectoryBot, MonitoringBotDirectory, MonitoringPrincipal, MonitoringReferences } from './directory-contracts.js';

export type BotEnabledNotifier = (bots: readonly DirectoryBot[]) => Promise<void>;

function newlyEnabled(version: number, previous: BotDiagnosisRecoveryConfig | null | undefined): boolean {
  return version === 0 || (previous?.version === version && !(previous.diagnosisEnabled && previous.recoveryEnabled));
}

function notifyEnabled(notify: BotEnabledNotifier | undefined, bots: readonly DirectoryBot[]): void {
  if (!notify || !bots.length) return;
  // A delivery failure cannot invalidate an already committed configuration.
  void Promise.resolve().then(() => notify(bots)).catch(() => {
    console.warn('[monitoring] Bot enablement notification failed', { count: bots.length });
  });
}

export function isServiceBot(bot: DirectoryBot): boolean {
  return bot.botType?.trim().toLowerCase() === 'service';
}

/** Shared by option rendering and owner writes, using the current directory metadata. */
export function botConfigurationDisabledReason(bot: DirectoryBot): string | undefined {
  if (bot.botType?.trim().toLowerCase() === 'desktop') return '桌面 Bot 暂不支持';
  const engine = bot.activeEngine.trim().toLowerCase();
  if (!['openclaw', 'teclaw'].includes(engine)) return '当前引擎暂不支持';
  if (isServiceBot(bot)) return '服务 Bot 请联系平台评估开启';
  if (bot.botType?.trim().toLowerCase() !== 'personal') return '当前 Bot 类型暂不支持';
  return undefined;
}

export async function replaceBotConfiguration(store: BotDiagnosisRecoveryConfigStore, bot: DirectoryBot, updatedBy: string, input: unknown, app?: RecoveryAppConfigStore, onBotEnabled?: BotEnabledNotifier) {
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
  const enabling = onBotEnabled && diagnosisEnabled && recoveryEnabled;
  const previous = enabling && version > 0 ? await store.find(bot) : null;
  const saved = await store.replaceConfiguration(bot, { diagnosisEnabled, recoveryEnabled, configJson, expectedVersion: version, updatedBy });
  if (enabling && newlyEnabled(version, previous)) notifyEnabled(onBotEnabled, [bot]);
  return saved;
}

/** Only current owners may write, including when the caller can read all Bots. */
export async function configureBots(store: BotDiagnosisRecoveryConfigStore | undefined, directory: MonitoringBotDirectory,
  references: MonitoringReferences, principal: MonitoringPrincipal, input: unknown, onBotEnabled?: BotEnabledNotifier) {
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
  const enabledBots: DirectoryBot[] = [];
  const write = async (bot: DirectoryBot, version: number, previous?: BotDiagnosisRecoveryConfig | null) => {
    if (bot.ownerId !== principal.staffId) throw new MonitoringError('FORBIDDEN', '仅 Bot 所有者可以修改配置。');
    if (bot.env !== 'prod') throw new MonitoringError('FORBIDDEN', '仅支持生产环境 Bot 的诊断自愈配置。');
    const disabledReason = botConfigurationDisabledReason(bot);
    if (disabledReason) throw new MonitoringError('FORBIDDEN', disabledReason);
    const switches = { diagnosisEnabled: value.enabled as boolean, recoveryEnabled: value.enabled as boolean,
      expectedVersion: version, updatedBy: principal.staffId };
    const enabling = onBotEnabled && switches.diagnosisEnabled;
    if (enabling && version > 0 && previous === undefined) previous = await store.find(bot);
    // Initialize only a new row. Existing settings remain administrator-owned, including historical empty JSON.
    if (version === 0) await store.replaceConfiguration(bot, { ...switches, configJson: JSON.stringify({
      team_key: 'default', notifications: { owner: { enabled: true }, group: { enabled: true } },
    }) });
    else await store.setSwitches(bot, switches);
    if (enabling && newlyEnabled(version, previous)) enabledBots.push(bot);
    updated++;
  };
  try {
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
          if (current.env !== 'prod' || botConfigurationDisabledReason(current)) continue;
          try { await write(current, configs[i]?.version ?? 0, configs[i]); }
          catch { failed.push(references.encode(bot, principal.tenant)); }
        }
        if (!page.hasMore || !page.items.length) break;
        after = page.items[page.items.length - 1].directoryId;
      }
    }
  } finally {
    notifyEnabled(onBotEnabled, enabledBots);
  }
  return { updated, failed };
}
