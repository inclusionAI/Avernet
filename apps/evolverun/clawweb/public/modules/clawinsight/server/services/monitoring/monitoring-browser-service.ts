import { MonitoringError, type MonitoringStore, type MonitoringSummary } from './contracts.js';
import type { BotOption, BotScope, DirectoryBot, DirectoryScope, MonitoringBotDirectory, MonitoringBrowserApi,
  MonitoringPrincipal, MonitoringReferences, MonitoringSpaceSettings } from './directory-contracts.js';
import { browserDiagnosisQuery, parseSpaceId, parseWindow, queryKeys } from './browser-validation.js';
import type { BotDiagnosisRecoveryConfig, BotDiagnosisRecoveryConfigStore } from './bot-config-contracts.js';
import { botConfigurationDisabledReason, configureBots, isServiceBot, replaceBotConfiguration, type BotEnabledNotifier } from './bot-config-service.js';
import { recoveryConfigurations } from '../repair/session-recovery/bot-configuration.js';
import type { RecoveryAppConfigStore } from '../repair/session-recovery/configuration-contracts.js';

export function createMonitoringBrowserService(store: MonitoringStore, directory: MonitoringBotDirectory,
  references: MonitoringReferences, storageScope: DirectoryScope, now: () => number = Date.now,
  configurations?: BotDiagnosisRecoveryConfigStore, legacyConfig?: RecoveryAppConfigStore, onBotEnabled?: BotEnabledNotifier,
  spaceSettings?: MonitoringSpaceSettings): MonitoringBrowserApi {
  function authorize(p: MonitoringPrincipal) {
    if (!p.isAuthenticated || !p.staffId?.trim()) throw new MonitoringError('UNAUTHENTICATED', '请先登录。');
    if (!p.tenant || p.tenant !== storageScope.tenant || !p.allowedTargetEnvs.length
      || p.allowedTargetEnvs.some(env => !storageScope.allowedTargetEnvs.includes(env))) {
      throw new MonitoringError('FORBIDDEN', '无权访问此监控范围。');
    }
  }
  async function visible(p: MonitoringPrincipal, ref: string) {
    authorize(p);
    const t = references.decode(ref, p.tenant);
    const bot = await directory.get(t, p);
    if (!bot || (!p.isClawInsightAdmin && bot.ownerId !== p.staffId)) {
      throw new MonitoringError('BOT_NOT_FOUND', 'Bot 不存在或无权访问。');
    }
    return bot;
  }
  async function configurationTarget(p: MonitoringPrincipal, ref: string) {
    authorize(p);
    if (!p.isClawInsightAdmin && !p.isGlobalAdmin) throw new MonitoringError('FORBIDDEN', '需要监控或全局管理员权限。');
    if (!configurations) throw new MonitoringError('NOT_READY', '诊断监控配置尚未就绪。');
    const bot = await directory.get(references.decode(ref, p.tenant), p);
    if (!bot) throw new MonitoringError('BOT_NOT_FOUND', 'Bot 不存在或无权访问。');
    return { bot, store: configurations };
  }
  function option(bot: DirectoryBot, summary: MonitoringSummary | null, tenant: string, config?: BotDiagnosisRecoveryConfig | null): BotOption {
    return { botRef: references.encode(bot, tenant), botName: bot.botName, botId: bot.botId, ownerId: bot.ownerId, env: bot.env,
      enrollmentState: summary ? 'ENROLLED' : 'NOT_ENROLLED', monitoring: summary,
      ...(isServiceBot(bot) && { isServiceBot: true }),
      activeEngine: bot.activeEngine, botType: bot.botType,
      configurationDisabledReason: botConfigurationDisabledReason(bot),
      configuration: { diagnosisEnabled: config?.diagnosisEnabled ?? false, recoveryEnabled: config?.recoveryEnabled ?? false, version: config?.version ?? 0 },
      capabilities: { canView: true, canRequestEnrollment: !summary } };
  }
  async function spaceOptions(p: MonitoringPrincipal, spaceId: string) {
    if (!spaceSettings) throw new MonitoringError('NOT_READY', '空间 Bot 设置尚未就绪。');
    return (await spaceSettings.listBots(p, spaceId)).filter(bot => p.allowedTargetEnvs.includes(bot.env));
  }
  async function spaceOption(bot: DirectoryBot, info: BotOption, p: MonitoringPrincipal, spaceId: string): Promise<BotOption> {
    const disabledReason = info.configurationDisabledReason ?? (bot.env !== 'prod' ? '仅支持生产环境 Bot 的诊断自愈配置' : undefined);
    const canConfigure = !disabledReason && Boolean(await spaceSettings!.configurableBot(p, spaceId, bot));
    return { ...info, canConfigure, configurationDisabledReason: disabledReason
      ?? (canConfigure ? undefined : '仅 Bot 所有者和已获编辑权限的协作者可以修改配置') };
  }
  function directoryIdBefore(left: string, right: string): boolean {
    return left.length < right.length || (left.length === right.length && left < right);
  }
  function matchesQuery(bot: DirectoryBot, q: string): boolean {
    if (!q) return true;
    const needle = q.toLowerCase();
    return [bot.botName, bot.botId, bot.ownerId].some(value => value.toLowerCase().includes(needle));
  }
  async function monitoredDirectory(q: string, after: string | null, limit: number, scope: DirectoryScope) {
    // Monitoring storage is the source of truth for enrollment. Resolve those
    // identities through the directory afterwards instead of scanning the newest
    // directory rows and hoping they contain every monitored target.
    const targets = await store.listCheckedTargets();
    const resolved = await Promise.all(targets.map(target => directory.get(target, scope)));
    const candidates = resolved.filter((bot): bot is DirectoryBot => bot !== null && matchesQuery(bot, q))
      .sort((a, b) => a.directoryId.length !== b.directoryId.length
        ? b.directoryId.length - a.directoryId.length
        : b.directoryId.localeCompare(a.directoryId));
    const eligible = after ? candidates.filter(bot => directoryIdBefore(bot.directoryId, after)) : candidates;
    return { items: eligible.slice(0, limit), hasMore: eligible.length > limit };
  }
  return {
    async configure(p, input) {
      authorize(p);
      return configureBots(configurations, directory, references, p, input, onBotEnabled, spaceSettings);
    },
    async readConfiguration(p, ref) {
      const { bot, store } = await configurationTarget(p, ref);
      return store.find(bot);
    },
    async replaceConfiguration(p, ref, input) {
      const { bot, store } = await configurationTarget(p, ref);
      return replaceBotConfiguration(store, bot, p.staffId, input, legacyConfig, onBotEnabled);
    },
    async options(p, query) {
      authorize(p);
      queryKeys(query, ['scope', 'q', 'limit', 'cursor', 'start', 'end', 'env', 'spaceId']);
      const spaceId = parseSpaceId(query.spaceId);
      const scope = (query.scope ?? 'mine') as BotScope;
      if (!['mine', 'all', 'monitored'].includes(scope)) throw new MonitoringError('INVALID_EVENT', 'Bot 范围无效。');
      if (scope !== 'mine' && !p.isClawInsightAdmin) throw new MonitoringError('FORBIDDEN', '无权访问此 Bot 范围。');
      if (spaceId && scope !== 'mine') throw new MonitoringError('INVALID_EVENT', '空间设置不支持切换 Bot 范围。');
      const env = query.env;
      if (env !== undefined && (typeof env !== 'string' || !p.allowedTargetEnvs.includes(env))) {
        throw new MonitoringError('INVALID_EVENT', 'Bot 环境无效。');
      }
      const principal = env === undefined ? p : { ...p, allowedTargetEnvs: [env] };
      const q = String(query.q ?? '').trim();
      const rawLimit = String(query.limit ?? '20');
      const limit = Number(rawLimit);
      if (q.length > 200 || !/^\d+$/.test(rawLimit) || limit < 1 || limit > 50) throw new MonitoringError('INVALID_EVENT', '搜索条件无效。');
      const window = parseWindow(query, now());
      const context = JSON.stringify([1, p.staffId, p.isClawInsightAdmin, p.tenant, [...principal.allowedTargetEnvs].sort(), scope, q, limit, window, ...(spaceId ? [spaceId] : [])]);
      let after = query.cursor ? references.readCursor(String(query.cursor), context) : null;
      let page: { items: DirectoryBot[]; hasMore: boolean };
      if (spaceId) {
        const candidates = (await spaceOptions(principal, spaceId))
          .filter(bot => matchesQuery(bot, q) && (!after || directoryIdBefore(bot.directoryId, after)))
          .sort((a, b) => directoryIdBefore(a.directoryId, b.directoryId) ? 1 : -1);
        page = { items: candidates.slice(0, limit), hasMore: candidates.length > limit };
      } else if (scope === 'monitored') {
        page = await monitoredDirectory(q, after, limit, principal);
      } else {
        page = await directory.search({ principal, scope, q, limit, after });
      }
      const summaries = spaceId ? [] : await store.summaries(page.items, window);
      const configs = configurations ? await recoveryConfigurations(configurations, page.items, legacyConfig) : undefined;
      const items = await Promise.all(page.items.map(async (bot, index) => {
        const info = option(bot, summaries[index] ?? null, p.tenant, configs?.[index]);
        return spaceId ? spaceOption(bot, info, principal, spaceId) : info;
      }));
      const nextAfter = page.items.length ? page.items[page.items.length - 1].directoryId : after;
      return { items, nextCursor: page.hasMore && nextAfter ? references.cursor(nextAfter, context) : null, window };
    },
    async status(p, ref, query) {
      const bot = await visible(p, ref);
      queryKeys(query, ['start', 'end']);
      const [summary] = await store.summaries([bot], parseWindow(query, now()));
      return option(bot, summary, p.tenant, configurations ? (await recoveryConfigurations(configurations, [bot], legacyConfig))[0] : undefined);
    },
    async diagnoses(p, ref, query) {
      const bot = await visible(p, ref);
      const parsed = browserDiagnosisQuery(query, now());
      return store.listDiagnoses(bot, parsed);
    },
    async enroll(p, input) {
      if (!input || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).join(',') !== 'botRef'
        || typeof (input as { botRef: unknown }).botRef !== 'string') throw new MonitoringError('INVALID_EVENT', 'Bot 引用无效。');
      const bot = await visible(p, (input as { botRef: string }).botRef);
      if ((await store.readStatus(bot)).check) throw new MonitoringError('ALREADY_ENROLLED', '此 Bot 已加入监控，请刷新状态。');
      throw new MonitoringError('ENROLLMENT_NOT_IMPLEMENTED', '加入监控功能正在开发中，敬请期待。');
    },
  };
}
