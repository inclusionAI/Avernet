import { digestCanonicalJson } from '@avernet/clawevolve/server/services/evolution/contracts';
import { RepairBatchError } from './repair-batch.js';

/** Existing deployment history serialization omits only these root metadata fields.
 * All unknown fields, nested values and array order remain part of the identity. */
function configuration(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new RepairBatchError('INVALID_INPUT', 'Invalid deployed workflow configuration');
  const content = { ...value } as Record<string, unknown>;
  for (const key of ['version', 'updatedAt']) {
    if (typeof content[key] === 'string' || typeof content[key] === 'number' && Number.isFinite(content[key])) delete content[key];
  }
  return content;
}

/** Bind the original analysis-compatible JSON digest to the actual tagged Pack.
 * This never replaces the independent raw-file, commit or tree integrity checks. */
export function repairProposalSpecDigest(taggedSpec: unknown, deploymentSpec: unknown): string {
  if (digestCanonicalJson(configuration(taggedSpec)) !== digestCanonicalJson(configuration(deploymentSpec))) {
    throw new RepairBatchError('SOURCE_CHANGED', '生效部署记录与 Git Pack 配置不一致，无法确认建议基线', { reasonCode: 'BASELINE_CONTENT_MISMATCH' });
  }
  return digestCanonicalJson(deploymentSpec);
}
