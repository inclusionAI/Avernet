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
  additionalProperties?: boolean;
};

export type OfficialStageDefinition = {
  stage: StageKey;
  name: string;
  description: string;
  extensionModes: StageExtensionMode[];
  postprocessWritablePaths: string[];
  inputSchema: JsonSchema;
  resultSchema: JsonSchema;
};

export type OfficialStageContracts = {
  schemaVersion: "clawevolve.stage-contracts/v1";
  stages: OfficialStageDefinition[];
};

const STAGES = new Set<StageKey>(["diagnose", "plan", "optimize"]);
const MODES = new Set<StageExtensionMode>(["preprocess", "postprocess", "replace"]);

function loadContracts(value: unknown): OfficialStageContracts {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Official Evolve Stage catalog must be an object");
  }
  const contracts = value as OfficialStageContracts;
  if (contracts.schemaVersion !== "clawevolve.stage-contracts/v1"
    || !Array.isArray(contracts.stages)) {
    throw new Error("Official Evolve Stage contracts are invalid");
  }
  const definitions = new Map(contracts.stages.map((item) => [item.stage, item]));
  if (definitions.size !== STAGES.size || [...STAGES].some((stage) => !definitions.has(stage))) {
    throw new Error("Official Evolve Stage contracts are incomplete");
  }
  for (const stage of contracts.stages) {
    if (!STAGES.has(stage.stage) || !stage.name?.trim() || !stage.description?.trim()
      || stage.inputSchema?.type !== "object" || stage.resultSchema?.type !== "object"
      || !Array.isArray(stage.extensionModes)
      || !Array.isArray(stage.postprocessWritablePaths)
      || stage.postprocessWritablePaths.some((path) => !path.trim() || path.includes(".."))
      || new Set(stage.extensionModes).size !== stage.extensionModes.length
      || stage.extensionModes.some((mode) => !MODES.has(mode))) {
      throw new Error(`Invalid official Stage definition: ${String(stage.stage)}`);
    }
  }
  return contracts;
}

export const officialStageContracts = loadContracts(catalogJson);

export function findOfficialStage(stage: string): OfficialStageDefinition | null {
  return officialStageContracts.stages.find((item) => item.stage === stage) ?? null;
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
    required: [...new Set([...(stage.inputSchema.required ?? []), "builtin_result"])],
    properties: {
      ...stage.inputSchema.properties,
      builtin_result: {
        ...stage.resultSchema,
        description: `平台内置${stage.name}已经生成的完整结果；后处理只需返回允许字段的增量修改`,
      },
    },
  };
}

function schemaAtPath(schema: JsonSchema, path: string): JsonSchema | null {
  let current: JsonSchema | undefined = schema;
  for (const segment of path.split(".")) {
    if (current.type !== "object") return null;
    current = current.properties?.[segment];
    if (!current) return null;
  }
  return current;
}

function addSchemaPath(root: JsonSchema, path: string, value: JsonSchema): void {
  const segments = path.split(".");
  let current = root;
  for (const [index, segment] of segments.entries()) {
    current.properties ??= {};
    if (index === segments.length - 1) {
      current.properties[segment] = value;
      return;
    }
    current.properties[segment] ??= { type: "object", properties: {}, additionalProperties: false };
    current = current.properties[segment];
  }
}

function partialSchema(schema: JsonSchema): JsonSchema {
  if (schema.type === "object") {
    return {
      ...schema,
      required: undefined,
      additionalProperties: false,
      properties: Object.fromEntries(
        Object.entries(schema.properties ?? {}).map(([key, value]) => [key, partialSchema(value)]),
      ),
    };
  }
  return structuredClone(schema);
}

function postprocessPatchSchema(stage: OfficialStageDefinition): JsonSchema {
  const patch: JsonSchema = { type: "object", properties: {}, additionalProperties: false };
  for (const path of stage.postprocessWritablePaths) {
    const schema = schemaAtPath(stage.resultSchema, path);
    if (!schema) throw new Error(`${stage.name}后处理开放了不存在的结果字段: ${path}`);
    addSchemaPath(patch, path, partialSchema(schema));
  }
  return patch;
}

export function stageExtensionResultSchema(
  stage: OfficialStageDefinition,
  mode: StageExtensionMode,
): JsonSchema {
  if (mode === "replace") return stage.resultSchema;
  if (mode === "postprocess") {
    return {
      type: "object",
      required: ["result_patch"],
      additionalProperties: false,
      properties: {
        result_patch: {
          ...postprocessPatchSchema(stage),
          description: "只返回本实现要补充或修正的字段；平台会与内置结果合并并校验完整结果",
        },
      },
    };
  }
  return {
    type: "object",
    required: ["summary", "changed"],
    additionalProperties: false,
    properties: {
      summary: { type: "string", description: "本次前置处理完成了什么" },
      changed: { type: "boolean", description: "是否修改了平台提供的候选资源" },
      changed_files: {
        type: "array",
        items: { type: "string" },
        description: "实际修改的候选文件；未修改时可省略",
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
    if (schema.additionalProperties === false) {
      const unknown = Object.keys(record).find((key) => !(key in (schema.properties ?? {})));
      if (unknown) return `${path}.${unknown} 不是允许的字段`;
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
