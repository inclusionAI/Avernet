import type { ActionExecutionContext } from "./types.js";

function buildSource(
  context: ActionExecutionContext,
  result?: Record<string, unknown>,
): Record<string, unknown> {
  const input = (context as ActionExecutionContext & { input?: unknown }).input ?? {
    params: context.params,
    files: [],
  };
  return {
    ...(context.templateAliases ?? {}),
    params: context.params,
    input,
    workflowData: context.workflowData,
    nodeOutput: context.nodeOutput,
    actionOutputs: context.actionOutputs,
    result: result ?? {},
    loop: context.loop,
    flowId: context.flowId,
    nodeId: context.nodeId,
    sessionKey: context.sessionKey,
    executionMode: context.executionMode,
    bcsGroupId: context.bcsGroupId,
    user: context.user,
  };
}

function lookup(path: string, source: Record<string, unknown>): unknown {
  const parts = path.replace(/\[(\d+)\]/g, ".$1").split(".");
  let current: unknown = source;
  for (const part of parts) {
    if (current == null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

function isTemplate(value: string): string | null {
  const match = value.match(/^\{\{([\w.@-]+(?:\.[\w.@-]+|\[\d+\])*)\}\}$/);
  return match ? match[1] : null;
}

export function resolveTemplateValue(
  value: unknown,
  context: ActionExecutionContext,
  result?: Record<string, unknown>,
): unknown {
  if (typeof value !== "string") return value;

  const source = buildSource(context, result);
  const path = isTemplate(value);
  if (path) {
    return lookup(path, source);
  }

  return value.replace(/\{\{([\w.@-]+(?:\.[\w.@-]+|\[\d+\])*)\}\}/g, (_match, templatePath: string) => {
    const resolved = lookup(templatePath, source);
    if (resolved == null) return "";
    if (typeof resolved === "object") return JSON.stringify(resolved);
    return String(resolved);
  });
}

export function resolveActionArgs(
  input: Record<string, unknown>,
  context: ActionExecutionContext,
): Record<string, unknown> {
  function walk(value: unknown): unknown {
    if (Array.isArray(value)) {
      return value.map(walk);
    }

    if (value != null && typeof value === "object") {
      const nested = Object.entries(value as Record<string, unknown>).reduce<Record<string, unknown>>(
        (acc, [key, val]) => {
          acc[key] = walk(val);
          return acc;
        },
        {},
      );
      return nested;
    }

    return resolveTemplateValue(value, context);
  }

  return walk(input) as Record<string, unknown>;
}

const FORBIDDEN_PATH_SEGMENTS = new Set(["__proto__", "prototype", "constructor"]);

function parseWorkflowDataPath(path: string): string[] {
  if (!path.startsWith("workflowData.")) {
    throw new Error(`saveAs target must start with workflowData.: ${path}`);
  }

  const segments = path.substring("workflowData.".length).split(".");
  if (segments.length === 0 || segments.some((segment) => !segment.trim())) {
    throw new Error(`saveAs target must include a non-empty workflowData path: ${path}`);
  }

  for (const segment of segments) {
    if (FORBIDDEN_PATH_SEGMENTS.has(segment)) {
      throw new Error(`saveAs target contains forbidden path segment: ${segment}`);
    }
  }

  return segments;
}

/**
 * 运行时 saveAs target 校验的公开入口(parseWorkflowDataPath 的纯校验封装)。
 * 导出仅为让 normalize 期校验与运行时校验共用同一规则(单一真相源),
 * 避免出现"validate 过了运行时抛"或"validate 报错但运行时合法"的假阳/假阴。
 * 规则若变更,两处同步——tests/saveas-liveness.test.ts 有等价性断言护栏。
 */
export function validateSaveAsTarget(path: string): void {
  parseWorkflowDataPath(path);
}

function setWorkflowDataPath(target: Record<string, unknown>, segments: string[], value: unknown): void {
  let current: Record<string, unknown> = target;
  for (const segment of segments.slice(0, -1)) {
    const next = current[segment];
    if (next == null || typeof next !== "object" || Array.isArray(next)) {
      current[segment] = {};
    }
    current = current[segment] as Record<string, unknown>;
  }
  current[segments[segments.length - 1]] = value;
}

export function applySaveAs(
  workflowData: Record<string, unknown>,
  saveAs: Record<string, string> | undefined,
  result: Record<string, unknown>,
  context: ActionExecutionContext,
): void {
  for (const [target, template] of Object.entries(saveAs ?? {})) {
    const segments = parseWorkflowDataPath(target);
    const resolved = resolveTemplateValue(template, context, result);
    console.error(`[applySaveAs] ${context.nodeId ?? "?"} → ${target} = ${JSON.stringify(resolved)} (template: ${template})`);
    setWorkflowDataPath(workflowData, segments, resolved);
  }
}
