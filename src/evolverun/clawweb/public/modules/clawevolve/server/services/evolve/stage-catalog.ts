import catalogJson from "../../resources/evolve/official-stage-catalog.json" with { type: "json" };

export type StageKey = "diagnose" | "plan" | "optimize";
export type StageExtensionMode = "preprocess" | "postprocess" | "replace";

export type JsonSchema = {
  type: "object" | "array" | "string" | "boolean" | "number" | "integer";
  description?: string;
  required?: string[];
  enum?: string[];
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
};

export type OfficialStageDefinition = {
  stage: StageKey;
  name: string;
  description: string;
  extensionModes: StageExtensionMode[];
  inputSchema: JsonSchema;
  resultSchema: JsonSchema;
};

export type OfficialEvolveCatalog = {
  schemaVersion: "clawevolve.stage-catalog/v1";
  template: {
    name: string;
    description: string;
    steps: Array<{ stage: StageKey; name: string }>;
  };
  stages: OfficialStageDefinition[];
};

const STAGES = new Set<StageKey>(["diagnose", "plan", "optimize"]);
const MODES = new Set<StageExtensionMode>(["preprocess", "postprocess", "replace"]);

function loadCatalog(value: unknown): OfficialEvolveCatalog {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Official Evolve Stage catalog must be an object");
  }
  const catalog = value as OfficialEvolveCatalog;
  if (catalog.schemaVersion !== "clawevolve.stage-catalog/v1"
    || !catalog.template?.name?.trim()
    || !Array.isArray(catalog.template.steps)
    || !Array.isArray(catalog.stages)) {
    throw new Error("Official Evolve Stage catalog is invalid");
  }
  const definitions = new Map(catalog.stages.map((item) => [item.stage, item]));
  for (const step of catalog.template.steps) {
    if (!STAGES.has(step.stage) || !definitions.has(step.stage)) {
      throw new Error(`Unknown official Stage: ${String(step.stage)}`);
    }
  }
  for (const stage of catalog.stages) {
    if (!STAGES.has(stage.stage) || !stage.name?.trim() || !stage.description?.trim()
      || stage.inputSchema?.type !== "object" || stage.resultSchema?.type !== "object"
      || !Array.isArray(stage.extensionModes)
      || new Set(stage.extensionModes).size !== stage.extensionModes.length
      || stage.extensionModes.some((mode) => !MODES.has(mode))) {
      throw new Error(`Invalid official Stage definition: ${String(stage.stage)}`);
    }
  }
  return catalog;
}

export const officialEvolveCatalog = loadCatalog(catalogJson);

export function findOfficialStage(stage: string): OfficialStageDefinition | null {
  return officialEvolveCatalog.stages.find((item) => item.stage === stage) ?? null;
}

export function isStageExtensionMode(value: unknown): value is StageExtensionMode {
  return typeof value === "string" && MODES.has(value as StageExtensionMode);
}

export function stageRuntimeInputSchema(
  stage: OfficialStageDefinition,
  mode: StageExtensionMode,
): JsonSchema {
  if (mode !== "postprocess") return stage.inputSchema;
  return {
    ...stage.inputSchema,
    required: [...new Set([...(stage.inputSchema.required ?? []), "stage_result"])],
    properties: {
      ...stage.inputSchema.properties,
      stage_result: {
        ...stage.resultSchema,
        description: `平台内置${stage.name}已经生成的结果；后处理可复核、补充或修正，并交付同结构的最终结果`,
      },
    },
  };
}

export function validateJsonSchema(schema: JsonSchema, value: unknown, path = "input"): string | null {
  const actualType = Array.isArray(value) ? "array" : value === null ? "null" : typeof value;
  if (schema.type === "object") {
    if (!value || actualType !== "object") return `${path} 必须是对象`;
    const record = value as Record<string, unknown>;
    for (const key of schema.required ?? []) {
      if (!(key in record) || record[key] == null) return `${path}.${key} 为必填项`;
    }
    for (const [key, propertySchema] of Object.entries(schema.properties ?? {})) {
      if (record[key] == null) continue;
      const error = validateJsonSchema(propertySchema, record[key], `${path}.${key}`);
      if (error) return error;
    }
  } else if (schema.type === "array") {
    if (!Array.isArray(value)) return `${path} 必须是数组`;
    if (schema.items) {
      for (let index = 0; index < value.length; index += 1) {
        const error = validateJsonSchema(schema.items, value[index], `${path}[${index}]`);
        if (error) return error;
      }
    }
  } else if (schema.type === "integer") {
    if (typeof value !== "number" || !Number.isInteger(value)) return `${path} 必须是整数`;
  } else if (schema.type === "number") {
    if (typeof value !== "number" || !Number.isFinite(value)) return `${path} 必须是数字`;
  } else if (actualType !== schema.type) {
    return `${path} 必须是${schema.type === "string" ? "字符串" : schema.type === "boolean" ? "布尔值" : schema.type}`;
  }
  if (schema.enum && !schema.enum.includes(String(value))) {
    return `${path} 只能是 ${schema.enum.join("、")}`;
  }
  return null;
}
