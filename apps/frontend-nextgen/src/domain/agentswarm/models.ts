/**
 * Model choices for a run and for an individual agent.
 *
 * Static by design: the gateway accepts any string for `model`/`model_name` and
 * there is no model-catalogue endpoint on this surface, so a fixed list would
 * silently exclude a deployment's newer model. The selector therefore offers
 * known names but always permits a typed one.
 */

/** Names the agentswarm scenarios are exercised with. */
export const KNOWN_MODELS: readonly string[] = [
  'DeepSeek-V4.1-Flash',
  'DeepSeek-V4-Pro',
  'GLM-5',
  'GLM-5.1',
  'GLM-5.2',
  'Kimi-K2.5',
  'Kimi-K2.6',
  'MiniMax-M2.7',
];

/** The sentinel the Select uses for "fall through to the next level". */
export const INHERIT_VALUE = '__inherit__';

export function isKnownModel(value: string | null | undefined): boolean {
  return Boolean(value) && KNOWN_MODELS.includes(String(value));
}

/**
 * Resolution order, highest first: the agent's own model, then the run-level
 * `model_name`, then the deployment default (unset).
 */
export function resolveModel(
  agentModel: string | null | undefined,
  runModelName: string | null | undefined,
): string | undefined {
  if (agentModel?.trim()) return agentModel.trim();
  if (runModelName?.trim()) return runModelName.trim();
  return undefined;
}