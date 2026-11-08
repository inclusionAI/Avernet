import { digestCanonicalJson } from '@avernet/clawevolve/server/services/evolution/contracts';
/** Analysis identity comes from the deployed configuration, not its Git serialization.
 * Keep the existing Host signature; raw file/commit/tree checks remain independent. */
export function repairProposalSpecDigest(_taggedSpec: unknown, deploymentSpec: unknown): string {
  return digestCanonicalJson(deploymentSpec);
}
