import { getCodingRuntimeGroupKey, isCodefuseRuntime } from './runtime';

export interface CodefuseAuthPlaceholderLike {
  id?: string | null;
  runtime?: string | null;
  provider?: string | null;
  provider_id?: string | null;
  name?: string | null;
  display_name?: string | null;
  caller_unauthorized?: boolean;
}

const CODEFUSE_RUNTIME_MARKERS = ['codefuse-antcc', 'codefuse-codex'] as const;

function getModelMarkers(model?: CodefuseAuthPlaceholderLike | null): string[] {
  if (!model) return [];
  return [model.runtime, model.provider, model.provider_id, model.name, model.display_name]
    .filter(Boolean)
    .map((value) => String(value).toLowerCase());
}

/**
 * 识别 /api/models 返回项对应的 CodeFuse runtime。
 * 后端可能把 CodeFuse 标识放在 runtime/provider/name/display_name 上；
 * 这里优先精确识别 codefuse-antcc / codefuse-codex，避免把 Codex 面板误归到 CC。
 */
export const getCodefuseRuntimeFromModel = (
  model?: CodefuseAuthPlaceholderLike | null,
): 'codefuse-antcc' | 'codefuse-codex' | '' => {
  const markers = getModelMarkers(model);
  for (const runtime of CODEFUSE_RUNTIME_MARKERS) {
    if (markers.some((value) => value.includes(runtime))) return runtime;
  }
  return '';
};

/**
 * /api/models uses an empty-id CodeFuse model as an authorization placeholder.
 * It represents auth state only and must not be treated as a selectable model.
 */
export const isCodefuseAuthPlaceholder = (model?: CodefuseAuthPlaceholderLike | null): boolean => {
  if (!model || model.id) return false;

  const markers = getModelMarkers(model);

  return markers.some((value) => isCodefuseRuntime(value) || value.includes('codefuse') || value.includes('antcc'));
};

export const hasCallerCodefuseUnauthorized = (models: CodefuseAuthPlaceholderLike[]): boolean =>
  models.some((model) => model.caller_unauthorized === true);

/**
 * 判断某个 Coding 高层引擎分组（cc/codex）的 CodeFuse 是否已有真实模型可用。
 * 只有对应 runtime 的 CodeFuse 返回项存在且 id 非空，才认为该分组已授权。
 */
export const hasAuthorizedCodefuseModelInGroup = (models: CodefuseAuthPlaceholderLike[], groupKey: string): boolean =>
  models.some((model) => {
    if (!model?.id) return false;
    const codefuseRuntime = getCodefuseRuntimeFromModel(model);
    return !!codefuseRuntime && getCodingRuntimeGroupKey(codefuseRuntime) === groupKey;
  });

/**
 * 判断某个 Coding 高层引擎分组是否出现过 CodeFuse 标识。
 * 空 id 占位或无任何标识都不能说明已授权；调用方可据此决定是否展示授权入口。
 */
export const hasCodefuseMarkerInGroup = (models: CodefuseAuthPlaceholderLike[], groupKey: string): boolean =>
  models.some((model) => {
    const codefuseRuntime = getCodefuseRuntimeFromModel(model);
    return !!codefuseRuntime && getCodingRuntimeGroupKey(codefuseRuntime) === groupKey;
  });
