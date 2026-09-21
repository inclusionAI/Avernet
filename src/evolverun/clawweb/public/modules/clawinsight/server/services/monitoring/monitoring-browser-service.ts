import { MonitoringError, type MonitoringStore, type MonitoringSummary } from './contracts.js';
import type { BotOption, BotScope, DirectoryBot, DirectoryScope, MonitoringBotDirectory, MonitoringBrowserApi,
  MonitoringPrincipal, MonitoringReferences } from './directory-contracts.js';
import { browserDiagnosisQuery, parseWindow, queryKeys } from './browser-validation.js';

export function createMonitoringBrowserService(store: MonitoringStore, directory: MonitoringBotDirectory,
  references: MonitoringReferences, storageScope: DirectoryScope, now: () => number = Date.now): MonitoringBrowserApi {
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
  function option(bot: DirectoryBot, summary: MonitoringSummary | null, tenant: string): BotOption {
    return { botRef: references.encode(bot, tenant), botName: bot.botName, botId: bot.botId, ownerId: bot.ownerId, env: bot.env,
      enrollmentState: summary ? 'ENROLLED' : 'NOT_ENROLLED', monitoring: summary,
      capabilities: { canView: true, canRequestEnrollment: !summary } };
  }
  return {
    async options(p, query) {
      authorize(p);
      queryKeys(query, ['scope', 'q', 'limit', 'cursor', 'start', 'end']);
      const scope = (query.scope ?? 'mine') as BotScope;
      if (!['mine', 'all', 'monitored'].includes(scope)) throw new MonitoringError('INVALID_EVENT', 'Bot 范围无效。');
      if (scope !== 'mine' && !p.isClawInsightAdmin) throw new MonitoringError('FORBIDDEN', '无权访问此 Bot 范围。');
      const q = String(query.q ?? '').trim();
      const rawLimit = String(query.limit ?? '20');
      const limit = Number(rawLimit);
      if (q.length > 200 || !/^\d+$/.test(rawLimit) || limit < 1 || limit > 50) throw new MonitoringError('INVALID_EVENT', '搜索条件无效。');
      const window = parseWindow(query, now());
      const context = JSON.stringify([1, p.staffId, p.isClawInsightAdmin, p.tenant, [...p.allowedTargetEnvs].sort(), scope, q, limit, window]);
      let after = query.cursor ? references.readCursor(String(query.cursor), context) : null;
      let more = false;
      const items: BotOption[] = [];
      // Cross-connection membership scan: bounded to 200 candidates, consumes only visited rows.
      for (let scanned = 0; scanned < 200 && items.length < limit;) {
        const batchSize = scope === 'monitored' ? Math.min(50, 200 - scanned) : limit;
        const batch = await directory.search({ principal: p, scope, q, limit: batchSize, after });
        const summaries = await store.summaries(batch.items, window);
        more = batch.hasMore;
        for (let i = 0; i < batch.items.length; i++) {
          const bot = batch.items[i];
          after = bot.directoryId; scanned++;
          if (scope !== 'monitored' || summaries[i]) items.push(option(bot, summaries[i], p.tenant));
          if (items.length === limit) { more = i < batch.items.length - 1 || batch.hasMore; break; }
        }
        if (!more || scope !== 'monitored' || !batch.items.length) break;
      }
      return { items, nextCursor: more && after ? references.cursor(after, context) : null, window };
    },
    async status(p, ref, query) {
      const bot = await visible(p, ref);
      queryKeys(query, ['start', 'end']);
      const [summary] = await store.summaries([bot], parseWindow(query, now()));
      return option(bot, summary, p.tenant);
    },
    async diagnoses(p, ref, query) {
      const bot = await visible(p, ref);
      const parsed = browserDiagnosisQuery(query, now());
      if (!(await store.readStatus(bot)).check) throw new MonitoringError('NOT_ENROLLED', '此 Bot 尚未加入监控。');
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
