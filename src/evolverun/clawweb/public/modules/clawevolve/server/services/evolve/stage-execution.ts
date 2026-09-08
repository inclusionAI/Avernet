import type { StageExtensionMode, StageKey } from "./stage-catalog.js";

export type FrozenStageExtensionBinding = {
  enabled: boolean;
  implementationId: string;
};

export type FrozenStageExtensions = Partial<Record<StageExtensionMode, FrozenStageExtensionBinding>>;
export type FrozenTaskStageExtensions = Partial<Record<StageKey, FrozenStageExtensions>>;
export type StageExecutionPhase = "start" | "preprocess" | "builtin" | "replace" | "postprocess";

export type StageExecutionDecision =
  | { kind: "extension"; mode: StageExtensionMode; binding: FrozenStageExtensionBinding }
  | { kind: "builtin" }
  | { kind: "complete" };

function enabled(
  extensions: FrozenStageExtensions,
  mode: StageExtensionMode,
): FrozenStageExtensionBinding | null {
  const binding = extensions[mode];
  return binding?.enabled === true && binding.implementationId.trim() ? binding : null;
}

/**
 * Decide only the cut inside one fixed Stage. The surrounding Diagnose → Plan →
 * Optimize flow remains explicit in evolve.ts.
 */
export function nextStageExecution(
  extensions: FrozenStageExtensions,
  completed: StageExecutionPhase,
): StageExecutionDecision {
  if (completed === "start") {
    const preprocess = enabled(extensions, "preprocess");
    if (preprocess) return { kind: "extension", mode: "preprocess", binding: preprocess };
    const replace = enabled(extensions, "replace");
    return replace
      ? { kind: "extension", mode: "replace", binding: replace }
      : { kind: "builtin" };
  }
  if (completed === "preprocess") {
    const replace = enabled(extensions, "replace");
    return replace
      ? { kind: "extension", mode: "replace", binding: replace }
      : { kind: "builtin" };
  }
  if (completed === "builtin" || completed === "replace") {
    const postprocess = enabled(extensions, "postprocess");
    return postprocess
      ? { kind: "extension", mode: "postprocess", binding: postprocess }
      : { kind: "complete" };
  }
  return { kind: "complete" };
}
