/** Host composition for notification-only recovery. No identity or approval is
 * inferred from legacy admin flags, arbitrary headers, or callback config CRUD. */
import type { Express } from 'express';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { CallbackCompensationService } from '@avernet/workflow/server/services/callback-compensation/service';
import { fetchTransport } from '@avernet/workflow/server/services/callback-compensation/transport';
import type { CompensationEvidenceProvider } from '@avernet/workflow/server/services/callback-compensation/types';
import { callbackCompensationSecurity, createCallbackCompensationsRouter,
  type CallbackCompensationPrincipalResolver } from '@avernet/workflow/server/routes/callback-compensations';

export type CallbackCompensationHost = {
  principal: CallbackCompensationPrincipalResolver;
  trustedOrigins: readonly string[];
  /** Trusted, independently reviewed historical routing/generation/receiver evidence. */
  evidence?: CompensationEvidenceProvider;
  /** A stable 32-byte host secret, separate from HTTP receiver signing secrets. */
  protectionKey?: Buffer;
};
export function createCallbackCompensationRuntime(db: IDatabase, environment: string, host?: CallbackCompensationHost) {
  const service = new CallbackCompensationService(db, {
    environment, evidence: host?.evidence, protectionKey: host?.protectionKey, transport: fetchTransport,
  });
  const security = callbackCompensationSecurity(host?.trustedOrigins ?? []);
  const router = createCallbackCompensationsRouter({ service, principal: host?.principal ?? (async () => null) });
  let timer: ReturnType<typeof setInterval> | undefined;
  let current = Promise.resolve();
  let running = false;
  let stopped = false;
  return {
    /** Must precede generic CORS/body logging; authentication runs in the router. */
    mountSecurity(app: Express) { app.use('/api/workflows', security); },
    mountRoutes(app: Express) { app.use('/api/workflows', router); },
    start() {
      if (timer || stopped || !host?.evidence || !host.protectionKey) return;
      const tick = () => {
        if (running || stopped) return;
        running = true;
        current = service.tick().catch(() => {
          // Never log payloads, destination URLs, errors, or protected configuration.
          console.warn('[callback-compensation] worker tick failed');
        }).finally(() => { running = false; });
      };
      timer = setInterval(tick, 5000); timer.unref(); tick();
    },
    async stop() {
      stopped = true;
      if (timer) clearInterval(timer);
      await service.close();
      await current;
    },
  };
}
