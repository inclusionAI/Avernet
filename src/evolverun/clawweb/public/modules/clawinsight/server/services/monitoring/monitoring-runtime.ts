/** Composition root. Host-owned connections/identity; lazy, fail-closed, no implicit DB fallback. */
import type { Request } from 'express';
import { getRepositories, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { MonitoringRepository } from '../../repositories/monitoring-repository.js';
import { type MonitoringApi, MonitoringError } from './contracts.js';
import type { DirectoryScope, LegacyReportBinding, MonitoringBotDirectory, MonitoringBrowserApi, MonitoringPrincipal } from './directory-contracts.js';
import { createMonitoringService } from './monitoring-service.js';
import { createMonitoringBrowserService } from './monitoring-browser-service.js';
import { createTargetResolver } from './target-resolver.js';
import { createMonitoringReferences } from './target-ref.js';

/** Trusted host integration, registered at bootstrap, not from request JSON/headers. */
export type MonitoringIntegration = {
  directory: MonitoringBotDirectory;
  scope: DirectoryScope;
  /** Confirms monitoring storage is exclusively routed to scope.tenant. */
  isolatedStorageTenant: string;
  bindings?: readonly LegacyReportBinding[];
  principal: (request: Request) => Promise<MonitoringPrincipal>;
};
let configuredIntegration: MonitoringIntegration | null = null;
/** Optional composition hook; existing createInsightRouter calls stay source-compatible.
 * Until the deployed host provider is connected here, default requests return NOT_READY.
 */
export function configureMonitoringIntegration(integration: MonitoringIntegration): void {
  if (configuredIntegration) throw new Error('Monitoring integration is already configured; restart to change bindings');
  configuredIntegration = integration;
}
export type MonitoringRuntime = {
  service: MonitoringApi | null;
  browser?: MonitoringBrowserApi;
  principal?: (request: Request) => Promise<MonitoringPrincipal>;
};
export function createMonitoringRuntime(
  getDb: () => IDatabase = () => getRepositories().db,
  now: () => number = Date.now,
  supplied?: MonitoringIntegration,
): MonitoringRuntime {
  let assembled: { service: MonitoringApi; browser: MonitoringBrowserApi; integration: MonitoringIntegration } | null = null;
  const services = () => {
    if (assembled) return assembled;
    try {
      const integration = supplied ?? configuredIntegration;
      if (!integration || !integration.scope.tenant || !integration.scope.allowedTargetEnvs.length
        || integration.isolatedStorageTenant !== integration.scope.tenant) {
        throw new MonitoringError('NOT_READY', '监控可信身份、目录和租户隔离配置尚未就绪。');
      }
      const repository = new MonitoringRepository(getDb());
      const resolver = createTargetResolver(integration.directory, integration.scope, integration.bindings);
      const references = createMonitoringReferences(now);
      assembled = { integration, service: createMonitoringService(repository, resolver, now),
        browser: createMonitoringBrowserService(repository, integration.directory, references, integration.scope, now) };
      return assembled;
    } catch (error) {
      if (error instanceof MonitoringError) throw error;
      throw new MonitoringError('NOT_READY', '监控依赖尚未初始化。');
    }
  };
  return {
    service: {
      bots: async () => services().service.bots(),
      reportCheck: async (...args) => services().service.reportCheck(...args),
      reportDiagnosis: async (...args) => services().service.reportDiagnosis(...args),
      status: async (...args) => services().service.status(...args),
      diagnoses: async (...args) => services().service.diagnoses(...args),
    },
    browser: {
      options: async (...args) => services().browser.options(...args),
      status: async (...args) => services().browser.status(...args),
      diagnoses: async (...args) => services().browser.diagnoses(...args),
      enroll: async (...args) => services().browser.enroll(...args),
    },
    principal: async req => services().integration.principal(req),
  };
}
