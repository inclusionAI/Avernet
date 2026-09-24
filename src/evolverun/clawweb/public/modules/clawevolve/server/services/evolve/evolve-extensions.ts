import type { SkillTaskHostAction, HostSkillTarget, HostStageImplementation, SkillTaskHostPresetContext, SkillTaskHostPreset, TaskHostPresentation, TaskHostPresentationContext, EvolveExtension } from "../../contracts/evolve-extension.js";

function oneMatch<T>(matches: Array<{ extensionId: string; value: T }>): ({ extensionId: string } & T) | null {
  if (matches.length > 1) {
    throw new Error(`多个宿主扩展同时匹配当前任务: ${matches.map((match) => match.extensionId).join(", ")}`);
  }
  return matches[0] ? { extensionId: matches[0].extensionId, ...matches[0].value } : null;
}

export function resolveSkillTaskHostPreset(
  extensions: readonly EvolveExtension[],
  context: SkillTaskHostPresetContext,
): (SkillTaskHostPreset & { extensionId: string }) | null {
  return oneMatch(extensions.flatMap((extension) => {
    const value = extension.resolveSkillTaskPreset?.(context);
    return value ? [{ extensionId: extension.id, value }] : [];
  }));
}

export function resolveTaskHostPresentation(
  extensions: readonly EvolveExtension[],
  context: TaskHostPresentationContext,
): TaskHostPresentation | undefined {
  return oneMatch(extensions.flatMap((extension) => {
    const value = extension.resolveTaskPresentation?.(context);
    return value ? [{ extensionId: extension.id, value }] : [];
  })) ?? undefined;
}
