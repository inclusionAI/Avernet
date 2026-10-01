export type EvolveModelConfig = {
  defaultModel: string;
  models: readonly string[];
  repairModels?: readonly string[];
};

function normalizeModel(value: unknown): string {
  const model = typeof value === "string" ? value.trim() : "";
  if (!model || model.length > 128 || /[\0\r\n\s]/.test(model)) return "";
  return model;
}

function normalizeModels(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(normalizeModel).filter(Boolean))];
}

/** Normalize host-owned model configuration without introducing provider defaults. */
export function normalizeEvolveModelConfig(value: unknown): EvolveModelConfig {
  const input = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
  const defaultModel = normalizeModel(input.defaultModel);
  const models = normalizeModels(input.models);
  const repairModels = normalizeModels(input.repairModels);
  return {
    defaultModel,
    models: defaultModel && !models.includes(defaultModel) ? [defaultModel, ...models] : models,
    ...(repairModels.length > 0 ? {
      repairModels: defaultModel && !repairModels.includes(defaultModel)
        ? [defaultModel, ...repairModels]
        : repairModels,
    } : {}),
  };
}
