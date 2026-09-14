import {
  stageExtensionResultSchema,
  validateJsonSchema,
  type OfficialStageDefinition,
  type StageExtensionMode,
  type StageKey,
} from "./stage-catalog.js";

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

export type StageExtensionCompletion =
  | { kind: "preprocessed"; receipt: Record<string, unknown> }
  | { kind: "stage_output"; output: Record<string, unknown> };

function enabled(
  extensions: FrozenStageExtensions,
  mode: StageExtensionMode,
): FrozenStageExtensionBinding | null {
  const binding = extensions[mode];
  return binding?.enabled === true && binding.implementationId.trim() ? binding : null;
}

export function validateStageExtensions(extensions: FrozenStageExtensions): void {
  const replace = enabled(extensions, "replace");
  if (replace && (enabled(extensions, "preprocess") || enabled(extensions, "postprocess"))) {
    throw new Error("整体替换不能与前置处理或后置处理同时启用");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/** Resolve the inspected entrypoint without rewriting historical uploaded ZIPs. */
export function resolveStageImplementationEntrypoint(staticValidationJson: string): string {
  const inspection: unknown = JSON.parse(staticValidationJson);
  if (!isRecord(inspection)) throw new Error("Stage Skill 校验记录必须是对象");
  const manifest = inspection.manifest;
  const entrypoint = isRecord(manifest) && manifest.entrypoint !== undefined
    ? manifest.entrypoint
    : "implementation/SKILL.md";
  if (typeof entrypoint !== "string" || entrypoint.includes("\\") || entrypoint.includes("\0")) {
    throw new Error("Stage Skill 入口路径无效");
  }
  const segments = entrypoint.split("/");
  if (segments.length > 2 || segments.at(-1) !== "SKILL.md"
    || segments.some((segment) => !segment || segment === "." || segment === "..")) {
    throw new Error("Stage Skill 入口路径无效");
  }
  return entrypoint;
}

function leafPaths(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, child]) => {
    if (key === "__proto__" || key === "prototype" || key === "constructor") {
      throw new Error(`不允许修改结果字段: ${prefix ? `${prefix}.` : ""}${key}`);
    }
    const path = prefix ? `${prefix}.${key}` : key;
    return isRecord(child) && Object.keys(child).length > 0 ? leafPaths(child, path) : [path];
  });
}

function mergePatch(base: Record<string, unknown>, patch: Record<string, unknown>): Record<string, unknown> {
  const result = structuredClone(base);
  for (const [key, value] of Object.entries(patch)) {
    const current = result[key];
    result[key] = isRecord(current) && isRecord(value)
      ? mergePatch(current, value)
      : structuredClone(value);
  }
  return result;
}

/**
 * Post-processors return only the small business delta they own. The platform
 * applies that delta to the built-in result and validates the merged Stage
 * Output afterwards.
 */
export function mergeStageResultPatch(
  builtinOutput: Record<string, unknown>,
  resultPatch: Record<string, unknown>,
  writablePaths: string[],
): Record<string, unknown> {
  const allowed = new Set(writablePaths);
  for (const path of leafPaths(resultPatch)) {
    if (![...allowed].some((candidate) => path === candidate || path.startsWith(`${candidate}.`))) {
      throw new Error(`不允许修改结果字段: ${path}`);
    }
  }
  return mergePatch(builtinOutput, resultPatch);
}

/**
 * Keep the public extension contract deliberately smaller than the Stage
 * contract. Only a replacement owns a full Stage result. Pre/post extensions
 * report their own small delta and the platform builds the final result.
 */
export function completeStageExtension(input: {
  stage: OfficialStageDefinition;
  mode: StageExtensionMode;
  result: Record<string, unknown>;
  builtinOutput?: Record<string, unknown> | null;
}): StageExtensionCompletion {
  const contractError = validateJsonSchema(
    stageExtensionResultSchema(input.stage, input.mode),
    input.result,
    "result",
  );
  if (contractError) throw new Error(contractError);

  if (input.mode === "preprocess") {
    if (!String(input.result.summary ?? "").trim()) {
      throw new Error("result.summary 不能为空");
    }
    return { kind: "preprocessed", receipt: structuredClone(input.result) };
  }
  if (input.mode === "replace") {
    return { kind: "stage_output", output: structuredClone(input.result) };
  }
  if (!input.builtinOutput) {
    throw new Error("后处理缺少平台内置 Stage 结果");
  }
  const patch = input.result.result_patch;
  if (!isRecord(patch)) throw new Error("result.result_patch 必须是对象");
  const output = mergeStageResultPatch(
    input.builtinOutput,
    patch,
    input.stage.postprocessWritablePaths,
  );
  const outputError = validateJsonSchema(input.stage.resultSchema, output, "stage_output");
  if (outputError) throw new Error(outputError);
  return { kind: "stage_output", output };
}

/**
 * Decide only the cut inside one fixed Stage. The surrounding Diagnose → Plan →
 * Optimize flow remains explicit in evolve.ts.
 */
export function nextStageExecution(
  extensions: FrozenStageExtensions,
  completed: StageExecutionPhase,
): StageExecutionDecision {
  validateStageExtensions(extensions);
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
  if (completed === "builtin") {
    const postprocess = enabled(extensions, "postprocess");
    return postprocess
      ? { kind: "extension", mode: "postprocess", binding: postprocess }
      : { kind: "complete" };
  }
  return { kind: "complete" };
}
