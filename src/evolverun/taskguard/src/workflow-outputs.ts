import type { WorkflowOutputsSpec } from "./types.js";

export type WorkflowOutputsContext = Record<string, unknown>;

export type ResolvedWorkflowOutputs = {
  values: Record<string, unknown>;
  warnings: string[];
};

const TEMPLATE_PATH_RE = /^\{\{([\w.@-]+(?:\.[\w.@-]+|\[\d+\])*)\}\}$/u;
const EMBEDDED_TEMPLATE_RE = /\{\{([\w.@-]+(?:\.[\w.@-]+|\[\d+\])*)\}\}/gu;

function templatePath(value: string): string | null {
  const match = TEMPLATE_PATH_RE.exec(value.trim());
  return match ? match[1] : null;
}

function lookupPath(path: string, context: WorkflowOutputsContext): { found: boolean; value: unknown } {
  const parts = path.replace(/\[(\d+)\]/g, ".$1").split(".");
  let current: unknown = context;

  for (const part of parts) {
    if (current == null || typeof current !== "object") {
      return { found: false, value: undefined };
    }

    const record = current as Record<string, unknown>;
    if (!Object.prototype.hasOwnProperty.call(record, part)) {
      return { found: false, value: undefined };
    }

    current = record[part];
  }

  return { found: true, value: current };
}

function warningFor(name: string, path: string): string {
  return `outputs.${name}: source path "${path}" is missing`;
}

export function resolveWorkflowOutputs(
  outputs: WorkflowOutputsSpec | undefined,
  context: WorkflowOutputsContext,
): ResolvedWorkflowOutputs {
  const values: Record<string, unknown> = {};
  const warnings: string[] = [];

  for (const [name, spec] of Object.entries(outputs ?? {})) {
    const exactTemplatePath = templatePath(spec.from);
    const path = exactTemplatePath ?? spec.from;
    const hasEmbeddedTemplate = spec.from.match(EMBEDDED_TEMPLATE_RE) != null;

    if (!exactTemplatePath && hasEmbeddedTemplate) {
      let missing = false;
      const value = spec.from.replace(EMBEDDED_TEMPLATE_RE, (_match, templateValuePath: string) => {
        const nested = lookupPath(templateValuePath, context);
        if (!nested.found) {
          warnings.push(warningFor(name, templateValuePath));
          missing = true;
          return "";
        }
        if (nested.value == null) return "";
        if (typeof nested.value === "object") return JSON.stringify(nested.value);
        return String(nested.value);
      });

      if (!missing) values[name] = value;
      continue;
    }

    const resolved = lookupPath(path, context);

    if (!resolved.found) {
      warnings.push(warningFor(name, path));
      continue;
    }

    values[name] = resolved.value;
  }

  return { values, warnings };
}

export function pickPublicWorkflowOutputs(
  outputs: WorkflowOutputsSpec | undefined,
  values: Record<string, unknown>,
): Record<string, unknown> {
  const publicValues: Record<string, unknown> = {};

  for (const [name, spec] of Object.entries(outputs ?? {})) {
    if (spec.public === true && Object.prototype.hasOwnProperty.call(values, name)) {
      publicValues[name] = values[name];
    }
  }

  return publicValues;
}
