import { MonitoringError, type MonitoringStore, type MonitoringSummary } from './contracts.js';
import type { BotOption, BotScope, DirectoryBot, DirectoryScope, MonitoringBotDirectory, MonitoringBrowserApi,
  MonitoringPrincipal, MonitoringReferences } from './directory-contracts.js';
import { browserDiagnosisQuery, parseWindow, queryKeys } from './browser-validation.js';
import type { BotDiagnosisRecoveryConfig, BotDiagnosisRecoveryConfigStore } from './bot-config-contracts.js';
import { botConfigurationDisabledReason, configureBots, isServiceBot, replaceBotConfiguration, type BotEnabledNotifier } from './bot-config-service.js';
import { recoveryConfigurations } from '../repair/session-recovery/bot-configuration.js';
import type { RecoveryAppConfigStore } from '../repair/session-recovery/configuration-contracts.js';

export function createMonitoringBrowserService(store: MonitoringStore, directory: MonitoringBotDirectory,
  references: MonitoringReferences, storageScope: DirectoryScope, now: () => number = Date.now,
  configurations?: BotDiagnosisRecoveryConfigStore, legacyConfig?: RecoveryAppConfigStore, onBotEnabled?: BotEnabledNotifier): MonitoringBrowserApi {
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
      configurationDisabledReason: botConfigurationDisabledReason(bot),
      configuration: { diagnosisEnabled: config?.diagnosisEnabled ?? false, recoveryEnabled: config?.recoveryEnabled ?? false, version: config?.version ?? 0 },
      capabilities: { canView: true, canRequestEnrollment: !summary } };
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
      return configureBots(configurations, directory, references, p, input, onBotEnabled);
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
      queryKeys(query, ['scope', 'q', 'limit', 'cursor', 'start', 'end', 'env']);
      const scope = (query.scope ?? 'mine') as BotScope;
      if (!['mine', 'all', 'monitored'].includes(scope)) throw new MonitoringError('INVALID_EVENT', 'Bot 范围无效。');
      if (scope !== 'mine' && !p.isClawInsightAdmin) throw new MonitoringError('FORBIDDEN', '无权访问此 Bot 范围。');
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
      const context = JSON.stringify([1, p.staffId, p.isClawInsightAdmin, p.tenant, [...principal.allowedTargetEnvs].sort(), scope, q, limit, window]);
      let after = query.cursor ? references.readCursor(String(query.cursor), context) : null;
      let page: { items: DirectoryBot[]; hasMore: boolean };
      if (scope === 'monitored') {
        page = await monitoredDirectory(q, after, limit, principal);
      } else {
        page = await directory.search({ principal, scope, q, limit, after });
      }
      const summaries = await store.summaries(page.items, window);
      const configs = configurations ? await recoveryConfigurations(configurations, page.items, legacyConfig) : undefined;
      const items = page.items.map((bot, index) => option(bot, summaries[index], p.tenant, configs?.[index]));
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
