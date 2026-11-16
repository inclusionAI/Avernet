/** Server-owned AutoHeal target selection, shared by execution and receipt signing. */
import type { ResolvedBaasConfig } from './db.js';

export type AutoHealBaasEnvironment = 'dev' | 'pre' | 'prod';

export function resolveAutoHealBaasEnvironment(value: unknown): AutoHealBaasEnvironment {
  // Compatibility is only for an absent field. Never infer the Bot target from
  // the control host's getCurrentEnv(), or treat empty/invalid input as absent.
  if (value === undefined) return 'prod';
  if (value === 'dev' || value === 'pre' || value === 'prod') return value;
  throw new Error('Invalid auto-heal BaaS environment');
}

export type AutoHealBaasTransport = {
  environment: AutoHealBaasEnvironment;
  apiKey: string;
  baseUrl: string;
  iamtoken: string;
};

/** Pure selection keeps composition-root resolver calls (and their mocks) at
 * the caller. Only the selected complete pair is authoritative; there is no
 * top-level or cross-environment fallback here. Legacy prod uses the already
 * resolved environments.prod compatibility fallback from resolveBaasConfig. */
export function selectAutoHealBaasTransport(
  autoHeal: { baasEnvironment: AutoHealBaasEnvironment },
  baas: Pick<ResolvedBaasConfig, 'environments' | 'iamtoken'>,
): AutoHealBaasTransport {
  try {
    const environment = resolveAutoHealBaasEnvironment(autoHeal.baasEnvironment);
    const pair = baas.environments[environment];
    const { apiKey, baseUrl } = pair;
    if (typeof apiKey !== 'string' || !apiKey || /[\s\u0000-\u001f\u007f-\u009f]/u.test(apiKey)
      || typeof baseUrl !== 'string' || !baseUrl || baseUrl.trim() !== baseUrl
      || /[\u0000-\u001f\u007f-\u009f]/u.test(baseUrl) || typeof baas.iamtoken !== 'string') {
      throw new Error('BAAS_NOT_CONFIGURED');
    }
    const url = new URL(baseUrl);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error('BAAS_NOT_CONFIGURED');
    }
    // Canonicalize the whole endpoint, including its path, removing only one
    // trailing slash. Copy scalars so callers cannot retain mutable config refs.
    return { environment, apiKey, baseUrl: url.href.replace(/\/$/, ''), iamtoken: baas.iamtoken };
  } catch { throw new Error('BAAS_NOT_CONFIGURED'); }
}
