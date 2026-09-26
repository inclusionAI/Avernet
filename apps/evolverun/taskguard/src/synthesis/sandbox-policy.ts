/**
 * Sandbox policy — enforces executor whitelist, secret detection,
 * and template injection prevention for synthesized workflows.
 */
import type { SynthesisConfig, WorkflowSpec, ValidationError } from "../types.js";

// ── Executor whitelist ──

/** Check whether an executor type is allowed under the sandbox policy. */
export function isExecutorAllowed(type: string, config: SynthesisConfig): boolean {
  return config.allowedExecutors.includes(type) || config.extraAllowedExecutors.includes(type);
}

/** Check all nodes in a workflow spec for disallowed executors. */
export function checkWorkflowSecurity(
  spec: WorkflowSpec,
  config: SynthesisConfig,
): ValidationError[] {
  const errors: ValidationError[] = [];

  // 1. Executor whitelist check
  for (let i = 0; i < spec.nodes.length; i++) {
    const node = spec.nodes[i];
    const execType = node.executor?.type;
    if (execType && !isExecutorAllowed(execType, config)) {
      errors.push({
        stage: "security",
        path: `nodes[${i}].executor.type`,
        message: `Executor '${execType}' is not allowed in synthesized workflows`,
        suggestion: `Use one of: ${config.allowedExecutors.join(", ")}`,
      });
    }
  }

  // 2. Hardcoded secret detection
  const secretErrors = detectSecrets(spec, config);
  errors.push(...secretErrors);

  // 3. Template injection detection
  const injectionErrors = detectTemplateInjection(spec, config);
  errors.push(...injectionErrors);

  // 4. Node count cap
  if (spec.nodes.length > config.maxNodeCount) {
    errors.push({
      stage: "security",
      path: "nodes",
      message: `Node count (${spec.nodes.length}) exceeds maximum (${config.maxNodeCount})`,
      suggestion: `Reduce the workflow to at most ${config.maxNodeCount} nodes`,
    });
  }

  return errors;
}

// ── Secret detection ──

/** Detect hardcoded secrets in executor params using configured regex patterns. */
export function detectSecrets(
  spec: WorkflowSpec,
  config: SynthesisConfig,
): ValidationError[] {
  const errors: ValidationError[] = [];
  if (config.secretPatterns.length === 0) return errors;

  const regexes = config.secretPatterns.map((p) => {
    try {
      return new RegExp(p);
    } catch {
      return null;
    }
  }).filter((r): r is RegExp => r !== null);

  for (let i = 0; i < spec.nodes.length; i++) {
    const node = spec.nodes[i];
    const exec = node.executor as Record<string, unknown> | undefined;
    if (!exec) continue;

    // Check all string fields in the executor for secret patterns
    const stringsToCheck = collectStringValues(exec);
    for (const { path: fieldPath, value } of stringsToCheck) {
      for (const regex of regexes) {
        if (regex.test(value)) {
          errors.push({
            stage: "security",
            path: `nodes[${i}].executor.${fieldPath}`,
            message: "Possible hardcoded secret detected",
            suggestion: "Use environment variables or configuration instead of hardcoded secrets",
          });
          // Only report one secret per field
          break;
        }
      }
    }
  }

  return errors;
}

// ── Template injection detection ──

/** Detect dangerous template expressions like {{process.env.SECRET}}. */
export function detectTemplateInjection(
  spec: WorkflowSpec,
  config: SynthesisConfig,
): ValidationError[] {
  const errors: ValidationError[] = [];
  if (config.forbiddenTemplatePaths.length === 0) return errors;

  // Regex to find {{...}} template expressions
  const templateRegex = /\{\{([^}]+)\}\}/g;

  for (let i = 0; i < spec.nodes.length; i++) {
    const node = spec.nodes[i];
    const exec = node.executor as Record<string, unknown> | undefined;
    if (!exec) continue;

    const stringsToCheck = collectStringValues(exec);
    for (const { path: fieldPath, value } of stringsToCheck) {
      let match: RegExpExecArray | null;
      templateRegex.lastIndex = 0; // reset for each string
      while ((match = templateRegex.exec(value)) !== null) {
        const expr = match[1].trim();
        for (const forbiddenPath of config.forbiddenTemplatePaths) {
          if (expr.startsWith(forbiddenPath) || expr.includes(forbiddenPath)) {
            errors.push({
              stage: "security",
              path: `nodes[${i}].executor.${fieldPath}`,
              message: `Template injection risk: forbidden reference '${forbiddenPath}'`,
              suggestion: `Only reference nodeOutput, skillRoot, or workflow variables — not ${forbiddenPath}`,
            });
            break;
          }
        }
      }
    }
  }

  return errors;
}

// ── Helpers ──

/** Recursively collect all string values from an object with their dot-notation paths. */
function collectStringValues(
  obj: Record<string, unknown>,
  prefix: string = "",
): Array<{ path: string; value: string }> {
  const results: Array<{ path: string; value: string }> = [];

  for (const [key, val] of Object.entries(obj)) {
    const currentPath = prefix ? `${prefix}.${key}` : key;
    if (typeof val === "string") {
      results.push({ path: currentPath, value: val });
    } else if (typeof val === "object" && val !== null && !Array.isArray(val)) {
      results.push(...collectStringValues(val as Record<string, unknown>, currentPath));
    } else if (Array.isArray(val)) {
      for (let j = 0; j < val.length; j++) {
        const item = val[j];
        if (typeof item === "string") {
          results.push({ path: `${currentPath}[${j}]`, value: item });
        } else if (typeof item === "object" && item !== null) {
          results.push(...collectStringValues(item as Record<string, unknown>, `${currentPath}[${j}]`));
        }
      }
    }
  }

  return results;
}