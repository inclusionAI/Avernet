import type { OutputContractSchema, OutputContractSpec } from "./types.js";

export type OutputContractValidationIssue = { path: string; message: string };

function actualType(value: unknown): string {
  if (Array.isArray(value)) return "array";
  if (value === null) return "null";
  return typeof value;
}

function isType(value: unknown, type: OutputContractSchema["type"]): boolean {
  switch (type) {
    case "object":
      return value != null && typeof value === "object" && !Array.isArray(value);
    case "array":
      return Array.isArray(value);
    case "string":
      return typeof value === "string";
    case "number":
      return typeof value === "number";
    case "boolean":
      return typeof value === "boolean";
  }
}

function enumContains(values: Array<string | number | boolean | null>, value: unknown): boolean {
  return values.some((item) => Object.is(item, value));
}

function childPath(path: string, key: string): string {
  return `${path}.${key}`;
}

function itemPath(path: string, index: number): string {
  return `${path}[${index}]`;
}

function validateSchema(
  schema: OutputContractSchema,
  value: unknown,
  path: string,
  issues: OutputContractValidationIssue[],
): void {
  if (value === null && schema.nullable) return;
  if (!isType(value, schema.type)) {
    issues.push({ path, message: `期望 ${schema.type}，实际 ${actualType(value)}` });
    return;
  }

  if (schema.enum && !enumContains(schema.enum, value)) {
    issues.push({ path, message: `值不在 enum 允许范围内` });
    return;
  }

  if (schema.type === "object") {
    const record = value as Record<string, unknown>;
    for (const key of schema.required ?? []) {
      if (record[key] === undefined) {
        issues.push({
          path: childPath(path, key),
          message: `缺少 required 字段 ${key}`,
        });
      }
    }
    for (const [key, propertySchema] of Object.entries(schema.properties ?? {})) {
      if (record[key] !== undefined) {
        validateSchema(propertySchema, record[key], childPath(path, key), issues);
      }
    }
    return;
  }

  if (schema.type === "array" && schema.items) {
    (value as unknown[]).forEach((item, index) => {
      validateSchema(schema.items as OutputContractSchema, item, itemPath(path, index), issues);
    });
  }
}

export function validateOutputContractResult(
  contract: OutputContractSpec | undefined,
  result: Record<string, unknown> | undefined,
  nodeId: string,
): OutputContractValidationIssue[] {
  if (!contract) return [];
  const path = `nodeStates.${nodeId}.result`;
  if (result === undefined && contract.required === false) return [];

  const issues: OutputContractValidationIssue[] = [];
  validateSchema(contract.schema, result, path, issues);
  return issues;
}

export function formatOutputContractIssues(
  nodeTitle: string,
  issues: OutputContractValidationIssue[],
): string {
  return `${nodeTitle} 输出不符合契约: ${issues.map((issue) => `${issue.path}: ${issue.message}`).join("; ")}`;
}
