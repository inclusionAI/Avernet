/**
 * The tenant an Agent Compute run belongs to.
 *
 * This gateway surface takes tenancy as an explicit request parameter rather
 * than deriving it from the authenticated principal, and this version targets a
 * single tenant, so it is a deployment constant rather than a per-user lookup.
 * `config/config.local.ts` injects `TEAMCLAW_AGENTSWARM_TENANT`; the fallback
 * keeps tests and builds without the define working on the same value.
 */
export const DEFAULT_AGENTSWARM_TENANT = 'teamclaw';

export function resolveAgentswarmTenant(): string {
  const injected = typeof TEAMCLAW_AGENTSWARM_TENANT === 'string' ? TEAMCLAW_AGENTSWARM_TENANT.trim() : '';
  return injected || DEFAULT_AGENTSWARM_TENANT;
}