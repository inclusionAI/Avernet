import type { BotSkillGateway } from "../../contracts/bot-skill-gateway.js";
import type { ObjectStore } from "../object-storage/oss-object-store.js";

export type EvolveHostCapabilities = { skillManagement: boolean; stageCustomization: boolean };

export function resolveEvolveHostCapabilities(input: {
  hostLocalSkills?: BotSkillGateway | null;
  artifactStore?: ObjectStore;
}): EvolveHostCapabilities {
  const available = Boolean(input.hostLocalSkills && input.artifactStore?.putObject);
  // Stage registration/test currently uses the same Skill package lifecycle.
  return { skillManagement: available, stageCustomization: available };
}
