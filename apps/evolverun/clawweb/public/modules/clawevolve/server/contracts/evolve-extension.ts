import type { FrozenSkillTarget } from "../services/evolve/skill-candidate.js";
import type { FrozenTaskStageExtensions } from "../services/evolve/stage-execution.js";
import type { StageExtensionMode, StageKey } from "../services/evolve/stage-catalog.js";

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
export type EvolveExtension = {
  id: string;
  resolveSkillTaskPreset?: (context: SkillTaskHostPresetContext) => SkillTaskHostPreset | null;
  resolveTaskPresentation?: (context: TaskHostPresentationContext) => Omit<TaskHostPresentation, "extensionId"> | null;
};
