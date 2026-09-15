import type { FrozenSkillTarget } from "./skill-candidate.js";
import type { FrozenTaskStageExtensions } from "./stage-execution.js";
import type { StageExtensionMode, StageKey } from "./stage-catalog.js";

export type SkillTaskHostAction = "diagnose" | "hardening" | "optimize";

export type HostSkillTarget = {
  assetId: string;
  botId: string;
  ownerUserId: string;
  displayName: string;
  spaceId: string | null;
  spaceType: "PERSONAL" | "TEAM" | null;
};

export type HostStageImplementation = {
  stageSkillId: string;
  implementationId: string;
  ownerUserId: string;
  displayName: string;
  spaceId: string | null;
  spaceType: "PERSONAL" | "TEAM" | null;
  stage: StageKey;
  mode: StageExtensionMode;
  versionNo: number;
  status: string;
  integrationTestStatus: string;
};

export type SkillTaskHostPresetContext = {
  action: SkillTaskHostAction;
  actorUserId: string;
  targetSkill: HostSkillTarget;
  availableStageImplementations: readonly HostStageImplementation[];
};

export type SkillTaskHostPreset = {
  stageExtensions?: FrozenTaskStageExtensions | null;
  unavailableReason?: string | null;
  launchDescription?: string | null;
};

export type TaskHostPresentation = {
  extensionId: string;
  data?: Record<string, unknown>;
};

export type TaskHostPresentationContext = {
  targetSkill: FrozenSkillTarget | undefined;
  stageExtensions: FrozenTaskStageExtensions;
};

/**
 * Optional behavior supplied by an embedding host. Avernet does not interpret
 * an extension's business meaning; without extensions it remains fully usable.
 */
export type EvolveHostExtension = {
  id: string;
  resolveSkillTaskPreset?: (context: SkillTaskHostPresetContext) => SkillTaskHostPreset | null;
  resolveTaskPresentation?: (context: TaskHostPresentationContext) => Omit<TaskHostPresentation, "extensionId"> | null;
};

function oneMatch<T>(matches: Array<{ extensionId: string; value: T }>): ({ extensionId: string } & T) | null {
  if (matches.length > 1) {
    throw new Error(`多个宿主扩展同时匹配当前任务: ${matches.map((match) => match.extensionId).join(", ")}`);
  }
  return matches[0] ? { extensionId: matches[0].extensionId, ...matches[0].value } : null;
}

export function resolveSkillTaskHostPreset(
  extensions: readonly EvolveHostExtension[],
  context: SkillTaskHostPresetContext,
): (SkillTaskHostPreset & { extensionId: string }) | null {
  return oneMatch(extensions.flatMap((extension) => {
    const value = extension.resolveSkillTaskPreset?.(context);
    return value ? [{ extensionId: extension.id, value }] : [];
  }));
}

export function resolveTaskHostPresentation(
  extensions: readonly EvolveHostExtension[],
  context: TaskHostPresentationContext,
): TaskHostPresentation | undefined {
  return oneMatch(extensions.flatMap((extension) => {
    const value = extension.resolveTaskPresentation?.(context);
    return value ? [{ extensionId: extension.id, value }] : [];
  })) ?? undefined;
}
