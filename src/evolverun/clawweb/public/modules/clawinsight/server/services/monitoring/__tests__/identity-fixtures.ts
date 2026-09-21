import type { MonitoringTarget, ResolvedReport } from '../contracts.js';
import type { MonitoringBotDirectory, MonitoringTargetResolver } from '../directory-contracts.js';
import type { MonitoringIntegration } from '../monitoring-runtime.js';
/** Explicit synthetic identity for v1 transport regressions only. Production never infers these values. */
export const testTarget = (botId: string): MonitoringTarget => ({ botId, entityId: '001234', env: 'test' });
export const resolved = <T extends { botId: string }>(wire: T): ResolvedReport<T> => ({ wire, target: testTarget(wire.botId) });
export const fixtureResolver: MonitoringTargetResolver = {
  resolve: async botId => testTarget(botId), legacyId: async target => target.botId,
};
export const fixtureDirectory: MonitoringBotDirectory = {
  exact: async botId => [{ ...testTarget(botId), directoryId: '1', botName: botId, ownerId: '001234', ownerName: null }],
  get: async target => ({ ...target, directoryId: '1', botName: target.botId, ownerId: '001234', ownerName: null }),
  search: async () => ({ items: [], hasMore: false }),
};
export const fixtureIntegration: MonitoringIntegration = {
  directory: fixtureDirectory, scope: { tenant: 'test', allowedTargetEnvs: ['test'] }, isolatedStorageTenant: 'test',
  principal: async () => ({ staffId: '001234', isAuthenticated: true, isClawInsightAdmin: false, tenant: 'test', allowedTargetEnvs: ['test'] }),
};
