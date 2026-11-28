import type { CompensationEvidence, CompensationEvidenceProvider } from '@avernet/workflow/server/services/callback-compensation/types';

/** Server-only, reviewed manifest. Never populate this from a browser, mutable
 * callback settings or a flag saying an operator accepts duplicate side effects.
 * Evidence references identify independently established contracts/history. */
export function reviewedCompensationEvidence(value: unknown, environment: string): CompensationEvidenceProvider {
  const invalid = () => new Error('Invalid callback compensation evidence manifest');
  const object = (v: unknown, keys: string[]): Record<string, unknown> => {
    if (!v || typeof v !== 'object' || Array.isArray(v) || Object.keys(v).some(k => !keys.includes(k))) throw invalid();
    return v as Record<string, unknown>;
  };
  const text = (v: unknown, max = 1024): string => {
    if (typeof v !== 'string' || !v.trim() || v.length > max || v.includes('\0')) throw invalid();
    return v;
  };
  const date = (v: unknown): string => {
    const s = text(v, 40);
    if (!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(s) || !Number.isFinite(Date.parse(s))) throw invalid();
    return s;
  };
  const manifest = object(value, ['version', 'environment', 'entries']);
  if (manifest.version !== 1 || manifest.environment !== environment || !Array.isArray(manifest.entries) || manifest.entries.length > 10000) throw invalid();
  const aliases = new Map<string, string>();
  const exactEntries = new Set<string>();
  const entries = manifest.entries.map(raw => {
    const e = object(raw, ['environment', 'workflowId', 'flowId', 'receiverId', 'generation', 'retryEpoch', 'snapshotHash',
      'generationEvidenceRef', 'configIds', 'urls', 'url', 'historicalRoute', 'legacyIdempotency', 'evidenceRef', 'validFrom', 'validUntil', 'phaseNullable']);
    for (const key of ['workflowId', 'flowId', 'receiverId', 'generation', 'generationEvidenceRef', 'evidenceRef']) text(e[key]);
    if (e.environment !== environment || typeof e.retryEpoch !== 'number' || !Number.isSafeInteger(e.retryEpoch) || e.retryEpoch < 0
      || typeof e.snapshotHash !== 'string' || !/^[a-f0-9]{64}$/.test(e.snapshotHash) || typeof e.phaseNullable !== 'boolean') throw invalid();
    const list = (v: unknown): string[] => {
      if (!Array.isArray(v) || !v.length || v.length > 100 || new Set(v).size !== v.length) throw invalid();
      return v.map(s => text(s));
    };
    const configs = list(e.configIds), urls = list(e.urls);
    for (const s of urls) {
      const url = new URL(s);
      if (url.protocol !== 'https:' || url.username || url.password || url.hash || url.hostname.includes('*')) throw invalid();
    }
    if (!urls.includes(text(e.url))) throw invalid();
    for (const alias of [...configs.map(s => `config:${s}`), ...urls.map(s => `url:${s}`)]) {
      const key = JSON.stringify([environment, e.workflowId, alias]);
      if (aliases.has(key) && aliases.get(key) !== e.receiverId) throw invalid();
      aliases.set(key, e.receiverId as string);
    }
    const route = object(e.historicalRoute, ['evidenceRef', 'validFrom', 'validUntil']);
    text(route.evidenceRef); date(route.validFrom); date(route.validUntil);
    const idem = object(e.legacyIdempotency, ['evidenceRef', 'keyContract', 'legacySenderContract', 'generationContract',
      'lateArrivalContract', 'earliestCompletion', 'latestCompletion', 'retentionUntil', 'lateArrivalUntil']);
    for (const k of ['evidenceRef', 'keyContract', 'legacySenderContract', 'generationContract', 'lateArrivalContract']) text(idem[k]);
    for (const k of ['earliestCompletion', 'latestCompletion', 'retentionUntil', 'lateArrivalUntil']) date(idem[k]);
    date(e.validFrom); date(e.validUntil);
    for (const [from, to] of [[e.validFrom, e.validUntil], [route.validFrom, route.validUntil],
      [idem.earliestCompletion, idem.latestCompletion], [idem.latestCompletion, idem.retentionUntil], [idem.lateArrivalUntil, idem.retentionUntil]]) {
      if (Date.parse(from as string) > Date.parse(to as string)) throw invalid();
    }
    for (const configId of configs) {
      const key = JSON.stringify([environment, e.workflowId, e.flowId, configId]);
      if (exactEntries.has(key)) throw invalid();
      exactEntries.add(key);
    }
    return structuredClone(e) as unknown as CompensationEvidence;
  });
  return { async resolve(q) {
    const found = entries.find(e => e.environment === q.environment && e.workflowId === q.workflowId && e.flowId === q.flowId
      && e.configIds.includes(q.configId) && e.snapshotHash === q.snapshotHash && e.retryEpoch === q.retryEpoch);
    return found ? structuredClone(found) : null;
  } };
}
