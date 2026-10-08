/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * template_config.support_engines 工具函数。
 *
 * support_engines 缺省时保持历史行为；只有显式传入非空数组时才按 runtime 过滤。
 *
 * 临时兼容：后端/模板可能传 support_engines: [] 表示“未配置限制”。当前先按“全部支持”处理，
 * 避免把模型列表过滤为空；待模板协议明确后再收敛空数组语义。
 */

import { getCodefuseRuntimeFromModel, hasAuthorizedCodefuseModelInGroup, isCodefuseAuthPlaceholder } from './codefuse';
import { getCodingRuntimeGroupKey, getModelRuntime, isCodefuseRuntime } from './runtime';

export type SupportEngines = string[] | undefined;

export function normalizeSupportEngines(value: unknown): SupportEngines {
  if (!Array.isArray(value)) return undefined;
  return Array.from(new Set(value.map((item) => (typeof item === 'string' ? item.trim() : '')).filter(Boolean)));
}

export function hasExplicitSupportEngines(supportEngines: SupportEngines): supportEngines is string[] {
  // 临时兼容：空数组按“未配置限制/全部支持”处理，不能当成“不支持任何引擎”。
  return Array.isArray(supportEngines) && supportEngines.length > 0;
}

export function isRuntimeSupported(runtime: string | null | undefined, supportEngines: SupportEngines): boolean {
  if (!hasExplicitSupportEngines(supportEngines)) return true;
  if (!runtime) return false;
  return supportEngines.includes(runtime);
}

export function supportEnginesAllowCodefuse(supportEngines: SupportEngines): boolean {
  if (!hasExplicitSupportEngines(supportEngines)) return true;
  return supportEngines.some(isCodefuseRuntime);
}

export function isCodefuseAuthPlaceholderSupported<
  T extends {
    runtime?: string | null;
    provider?: string | null;
    provider_id?: string | null;
    name?: string | null;
    display_name?: string | null;
  },
>(model: T, supportEngines: SupportEngines): boolean {
  if (!isCodefuseAuthPlaceholder(model)) return false;
  if (!supportEnginesAllowCodefuse(supportEngines)) return false;

  const placeholderRuntime = getCodefuseRuntimeFromModel(model);
  if (!placeholderRuntime) return true;
  return isRuntimeSupported(placeholderRuntime, supportEngines);
}

export function filterModelsBySupportEngines<T extends { runtime?: string | null; provider?: string | null }>(
  models: T[],
  supportEngines: SupportEngines,
  resolveRuntime: (model: T) => string = getModelRuntime,
): T[] {
  if (!hasExplicitSupportEngines(supportEngines)) return models;

  return models.filter((model) => {
    if (isCodefuseAuthPlaceholder(model)) {
      return isCodefuseAuthPlaceholderSupported(model, supportEngines);
    }
    return isRuntimeSupported(resolveRuntime(model), supportEngines);
  });
}

export function getCodefuseAuthRuntimeGroups(supportEngines: SupportEngines): string[] {
  return Array.from(
    new Set(
      [
        isRuntimeSupported('codefuse-antcc', supportEngines) ? getCodingRuntimeGroupKey('codefuse-antcc') : '',
        isRuntimeSupported('codefuse-codex', supportEngines) ? getCodingRuntimeGroupKey('codefuse-codex') : '',
      ].filter(Boolean),
    ),
  );
}

export function isCodefuseRuntimeGroupSupported(
  groupKey: string | null | undefined,
  supportEngines: SupportEngines,
): boolean {
  if (!groupKey) return false;
  return getCodefuseAuthRuntimeGroups(supportEngines).includes(groupKey);
}

export function shouldRenderCodingRuntimeGroup<
  T extends {
    id?: string | null;
    runtime?: string | null;
    provider?: string | null;
    provider_id?: string | null;
    name?: string | null;
    display_name?: string | null;
  },
>(params: {
  groupKey: string;
  groupModels: T[];
  supportEngines: SupportEngines;
  allModels: T[];
  isRuntimeLocked?: boolean;
}): boolean {
  const { groupKey, groupModels, supportEngines, allModels, isRuntimeLocked = false } = params;

  if (groupModels.some((model) => !!model.id)) return true;
  if (isRuntimeLocked) return false;
  if (!['cc', 'codex'].includes(groupKey)) return false;
  if (!isCodefuseRuntimeGroupSupported(groupKey, supportEngines)) return false;
  if (!groupModels.some(isCodefuseAuthPlaceholder)) return false;

  return !hasAuthorizedCodefuseModelInGroup(allModels, groupKey);
}
