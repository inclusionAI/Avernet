import { listCodefuseModelsForUser, type CodefuseModelDto } from '@/services/botWorkshop/agentCodingLegacyService';

/** 新建 Bot 时内置的 Claude Code（antchat）模型，来自 /api/models 的非 CodeFuse 返回。 */
export const ANTCHAT_MODELS: ModelOption[] = [
  {
    id: 'GLM-5.2',
    provider: 'antchat',
    name: 'GLM-5.2',
    display_name: 'GLM-5.2',
    displayName: 'GLM-5.2',
    runtime: 'claude-code',
  },
  {
    id: 'GLM-5.2[1m]',
    provider: 'antchat',
    name: 'GLM-5.2[1m]',
    display_name: 'GLM-5.2[1m]',
    displayName: 'GLM-5.2[1m]',
    runtime: 'claude-code',
  },
  {
    id: 'GLM-5.3[1m]',
    provider: 'antchat',
    name: 'GLM-5.3[1m]',
    display_name: 'GLM-5.3[1m]',
    displayName: 'GLM-5.3[1m]',
    runtime: 'claude-code',
  },
  {
    id: 'GLM-5.3-Flash[1m]',
    provider: 'antchat',
    name: 'GLM-5.3-Flash[1m]',
    display_name: 'GLM-5.3-Flash[1m]',
    displayName: 'GLM-5.3-Flash[1m]',
    runtime: 'claude-code',
  },
  {
    id: 'DeepSeek-V4-Pro',
    provider: 'antchat',
    name: 'DeepSeek V4 Pro',
    display_name: 'DeepSeek V4 Pro',
    displayName: 'DeepSeek V4 Pro',
    runtime: 'claude-code',
  },
  {
    id: 'Kimi-K2.5',
    provider: 'antchat',
    name: 'Kimi-K2.5',
    display_name: 'Kimi-K2.5',
    displayName: 'Kimi-K2.5',
    runtime: 'claude-code',
  },
  {
    id: 'DeepSeek-V4.1-Flash-1017',
    provider: 'antchat',
    name: 'DeepSeek-V4.1-Flash-1017',
    display_name: 'DeepSeek-V4.1-Flash-1017',
    displayName: 'DeepSeek-V4.1-Flash-1017',
    runtime: 'claude-code',
  },
];

/** 新建 Bot 时内置的 Codex 模型，来自 /api/models 的非 CodeFuse 返回，并保留 codex/ 前缀作为实际 ID。 */
export const CODEX_MODELS: ModelOption[] = [
  {
    id: 'codex/GLM-5.2',
    provider: 'codex',
    name: 'GLM-5.2',
    display_name: 'GLM-5.2',
    displayName: 'GLM-5.2',
    runtime: 'codex',
  },
  {
    id: 'codex/DeepSeek-V4.1-Flash-1017',
    provider: 'codex',
    name: 'DeepSeek-V4.1-Flash-1017',
    display_name: 'DeepSeek-V4.1-Flash-1017',
    displayName: 'DeepSeek-V4.1-Flash-1017',
    runtime: 'codex',
  },
  {
    id: 'codex/DeepSeek-V4-Pro',
    provider: 'codex',
    name: 'DeepSeek-V4-Pro',
    display_name: 'DeepSeek-V4-Pro',
    displayName: 'DeepSeek-V4-Pro',
    runtime: 'codex',
  },
  {
    id: 'codex/GLM-5.3',
    provider: 'codex',
    name: 'GLM-5.3',
    display_name: 'GLM-5.3',
    displayName: 'GLM-5.3',
    runtime: 'codex',
  },
  {
    id: 'codex/GLM-5.3-Flash',
    provider: 'codex',
    name: 'GLM-5.3-Flash',
    display_name: 'GLM-5.3-Flash',
    displayName: 'GLM-5.3-Flash',
    runtime: 'codex',
  },
  {
    id: 'codex/Kimi-K2.5',
    provider: 'codex',
    name: 'Kimi-K2.5',
    display_name: 'Kimi-K2.5',
    displayName: 'Kimi-K2.5',
    runtime: 'codex',
  },
];

export interface ModelOption {
  id: string;
  provider: string;
  name: string;
  display_name: string;
  description?: string | null;
  runtime?: string;
  displayName?: string;
  provider_id?: string | null;
  caller_unauthorized?: boolean;
}

const CODEFUSE_ENGINE_RUNTIME_MAP: Record<string, string> = {
  claude: 'codefuse-antcc',
  'claude-code': 'codefuse-antcc',
  claude_code: 'codefuse-antcc',
  antcc: 'codefuse-antcc',
  codex: 'codefuse-codex',
  'codefuse-antcc': 'codefuse-antcc',
  'codefuse-codex': 'codefuse-codex',
};

const normalizeCodefuseModelId = (modelId: unknown, runtime: string): string => {
  const id = String(modelId ?? '').trim();
  if (!id) return '';
  if (runtime !== 'codefuse-codex' || id.startsWith('codefuse-codex/source/builtin/')) {
    return id;
  }
  return `codefuse-codex/source/builtin/${id}`;
};

const getCodefuseModelEngines = (model: CodefuseModelDto): string[] => {
  const engines = Array.isArray(model.engine) ? model.engine : typeof model.engine === 'string' ? [model.engine] : [];
  if (engines.length > 0) {
    return engines.map((engine) => String(engine).trim().toLowerCase()).filter(Boolean);
  }
  if (typeof model.runtime === 'string' && model.runtime.trim()) {
    return [model.runtime.trim().toLowerCase()];
  }
  return ['claude'];
};

const normalizeCodefuseModel = (model: CodefuseModelDto, runtime: string): ModelOption => {
  const modelId = model.model ?? model.id ?? model.model_id ?? '';
  const name = String(model.modelName ?? model.name ?? model.model ?? model.model_id ?? '');
  const displayName = String(model.displayName ?? model.display_name ?? model.modelName ?? name);

  return {
    id: normalizeCodefuseModelId(modelId, runtime),
    provider: 'codefuse',
    runtime,
    name,
    display_name: displayName,
    displayName,
    description: typeof model.description === 'string' ? model.description : undefined,
  };
};

const expandCodefuseModelsByEngine = (models: CodefuseModelDto[]): ModelOption[] =>
  models
    .filter((model) => model.visible !== false)
    .flatMap((model) => {
      // 授权占位项没有真实模型 ID，不能按引擎展开丢失；保留给授权入口识别。
      if (model.caller_unauthorized === true && !model.id && !model.model && !model.model_id) {
        return [
          {
            id: '',
            provider: String(model.provider ?? 'codefuse'),
            name: String(model.name ?? model.display_name ?? ''),
            display_name: String(model.display_name ?? model.name ?? ''),
            runtime: typeof model.runtime === 'string' ? model.runtime : undefined,
            caller_unauthorized: true,
          },
        ];
      }

      return getCodefuseModelEngines(model)
        .map((engine) => CODEFUSE_ENGINE_RUNTIME_MAP[engine] ?? engine)
        .filter((runtime) => runtime === 'codefuse-antcc' || runtime === 'codefuse-codex')
        .map((runtime) => normalizeCodefuseModel(model, runtime));
    })
    .filter((model) => model.id || model.caller_unauthorized);

export async function fetchCfuseModels(userNo: string, modelsUrl: string): Promise<ModelOption[]> {
  if (!userNo || !modelsUrl) return [];

  try {
    const response = await listCodefuseModelsForUser(userNo, modelsUrl);
    const payload = response as any;
    const raw = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.data)
      ? payload.data
      : Array.isArray(payload?.models)
      ? payload.models
      : Array.isArray(payload?.data?.items)
      ? payload.data.items
      : Array.isArray(payload?.data?.models)
      ? payload.data.models
      : [];
    return expandCodefuseModelsByEngine(raw);
  } catch {
    // CodeFuse 未授权或服务暂不可用时，保留静态模型和授权入口，不阻塞表单。
    return [];
  }
}
