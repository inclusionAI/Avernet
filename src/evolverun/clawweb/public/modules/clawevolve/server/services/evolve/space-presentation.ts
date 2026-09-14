import type { FrozenSkillTarget } from "./skill-candidate.js";
import type { FrozenTaskStageExtensions } from "./stage-execution.js";

/** Configured at the host boundary using actual OCB IDs, never a space name. */
export type SpacePresentationPolicy = {
  spaceId: string;
  kind: "skill_hardening";
  diagnosePreprocessStageSkillId: string;
};

export function taskSpacePresentation(
  target: FrozenSkillTarget | undefined,
  extensions: FrozenTaskStageExtensions,
  policies: readonly SpacePresentationPolicy[],
) {
  const binding = extensions.diagnose?.preprocess;
  if (!target?.spaceId || target.spaceType !== "TEAM" || !binding?.enabled
    || binding.spaceId !== target.spaceId) return undefined;
  const policy = policies.find((item) => item.spaceId === target.spaceId
    && item.diagnosePreprocessStageSkillId === binding.stageSkillId);
  return policy ? { kind: policy.kind, spaceId: policy.spaceId, hardeningImplementationId: binding.implementationId } : undefined;
}
