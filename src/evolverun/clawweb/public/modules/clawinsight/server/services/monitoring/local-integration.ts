/** Explicit loopback demo dependencies. Never imported by production runtime/bootstrap. */
import type { MonitoringIntegration } from './monitoring-runtime.js';
import type { DirectoryBot } from './directory-contracts.js';
import { targetKey } from './target.js';

export function createLocalMonitoringIntegration(): MonitoringIntegration {
  const scope = { tenant: 'local-demo', allowedTargetEnvs: ['local'] };
  const bots: DirectoryBot[] = ['mock-bot-te', 'mock-bot-oc', 'mock-bot-not-enrolled'].map((botId, index) => ({
    directoryId: String(3 - index), activeEngine: botId === 'mock-bot-oc' ? 'openclaw' : 'teclaw', botId, entityId: 'local-demo', env: 'local',
    ownerId: 'local-demo', ownerName: 'Local demo', botName: botId,
  }));
  return {
    scope, isolatedStorageTenant: scope.tenant,
    principal: async () => ({ ...scope, staffId: 'local-demo', isAuthenticated: true, isClawInsightAdmin: true }),
    directory: {
      exact: async (id, s) => s.tenant === scope.tenant && s.allowedTargetEnvs.includes('local') ? bots.filter(b => b.botId === id) : [],
      get: async (target, s) => s.tenant === scope.tenant && s.allowedTargetEnvs.includes(target.env)
        ? bots.find(b => targetKey(b) === targetKey(target)) ?? null : null,
      async search(query) {
        const items = bots.filter(b => query.principal.tenant === scope.tenant && query.principal.allowedTargetEnvs.includes(b.env)
          && (query.scope !== 'mine' && query.principal.isClawInsightAdmin || b.ownerId === query.principal.staffId)
          && (query.after === null || Number(b.directoryId) < Number(query.after))
          && [b.botName, b.botId, ...(query.principal.isClawInsightAdmin ? [b.ownerId] : [])].some(v => v.toLowerCase().includes(query.q.toLowerCase())));
        return { items: items.slice(0, query.limit), hasMore: items.length > query.limit };
      },
    },
  };
}
