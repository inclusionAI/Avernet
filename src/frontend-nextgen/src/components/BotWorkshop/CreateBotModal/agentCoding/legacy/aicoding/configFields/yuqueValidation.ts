import {
  precheckYuqueInit,
  verifyYuqueBinding,
  type YuquePrecheckSource,
  type YuquePrecheckWarning,
} from '@/services/botWorkshop/agentCodingLegacyService';
import type { YuqueKbRepoItem } from './types';

export interface YuqueKbRepoValidationOptions {
  formatWarning?: (warning: YuquePrecheckWarning) => string;
  maxDocsWarningThreshold?: number;
  allowTeamYuqueExpand?: boolean;
}

export interface YuqueKbRepoValidationResult {
  success: boolean;
  errors: Record<number, string>;
}

export function normalizeYuqueUrl(url?: string | null): string {
  return (url || '').trim().replace(/\/+$/, '');
}

export function resolveYuquePrecheckWarningIndex(
  warning: YuquePrecheckWarning,
  sources: YuquePrecheckSource[] | undefined,
  urlToIndex: Map<string, number>,
  fallbackIndex: number,
): number {
  const warningUrl = normalizeYuqueUrl(warning.url);
  if (warningUrl && urlToIndex.has(warningUrl)) {
    return urlToIndex.get(warningUrl)!;
  }

  const matchedSources = (sources || []).filter((source) => urlToIndex.has(normalizeYuqueUrl(source.url)));

  if (warning.type === 'too_many_docs') {
    const estimatedDocCount = warning.estimatedDocCount;
    const source = matchedSources.find(
      (item) =>
        typeof estimatedDocCount === 'number' &&
        typeof item.docCount === 'number' &&
        item.docCount === estimatedDocCount,
    );
    if (source) {
      return urlToIndex.get(normalizeYuqueUrl(source.url))!;
    }
  }

  if (warning.type === 'team_url') {
    const source = matchedSources.find((item) => item.urlType === 'team' || item.reason === 'team_expand_disabled');
    if (source) {
      return urlToIndex.get(normalizeYuqueUrl(source.url))!;
    }
  }

  if (warning.type === 'count_failed' || warning.type === 'missing_token' || warning.type === 'invalid_url') {
    const source = matchedSources.find((item) => item.status === 'warning' || item.status === 'error');
    if (source) {
      return urlToIndex.get(normalizeYuqueUrl(source.url))!;
    }
  }

  if (matchedSources.length === 1) {
    return urlToIndex.get(normalizeYuqueUrl(matchedSources[0].url))!;
  }

  return fallbackIndex;
}

function mergeYuqueKbError(errors: Record<number, string>, index: number, message: string) {
  errors[index] = errors[index] ? `${errors[index]}；${message}` : message;
}

function formatDefaultYuquePrecheckWarning(warning: YuquePrecheckWarning): string {
  if (warning.message) return warning.message;

  switch (warning.type) {
    case 'team_url':
      return '该 URL 是团队 URL，建议改为具体知识库 URL';
    case 'too_many_docs':
      return '预计导入文档数过多，建议拆成具体知识库分批导入';
    case 'too_many_yuque_urls':
      return '语雀链接数量过多，建议减少链接数量或分批导入';
    case 'missing_token':
      return '缺少语雀 Token，无法统计文档数';
    case 'count_failed':
      return '知识库文档数统计失败，请检查 Token 权限后重试';
    case 'invalid_url':
      return '语雀 URL 无法解析，请检查地址是否正确';
    default:
      return '语雀知识库预检未通过，请检查后重试';
  }
}

export async function validateYuqueKbRepoBindings(
  repos: YuqueKbRepoItem[] = [],
  options: YuqueKbRepoValidationOptions = {},
): Promise<YuqueKbRepoValidationResult> {
  const errors: Record<number, string> = {};

  repos.forEach((repo, index) => {
    if (repo.url?.trim() && !repo.token?.trim()) {
      errors[index] = '填写了知识库地址时，Token 为必填项';
    }
  });

  const entriesToValidate = repos
    .map((repo, index) => ({ repo, index }))
    .filter(({ repo, index }) => repo.url?.trim() && repo.token?.trim() && !errors[index]);

  if (entriesToValidate.length === 0) {
    return { success: Object.keys(errors).length === 0, errors };
  }

  const bindingResults = await Promise.all(
    entriesToValidate.map(async ({ repo, index }) => {
      const url = repo.url!.trim();
      const token = repo.token!.trim();

      try {
        const result = await verifyYuqueBinding({ url, team_token: token });

        if (!result.success) {
          return { index, error: result.error || 'Token 验证失败' };
        }

        const { bound, login, namespace } = result.data!;
        if (!login) {
          return { index, error: 'Token 无效，无法获取用户信息' };
        }
        if (!bound) {
          return {
            index,
            error: `Token 对应用户 "${login}"，与 URL 路径 "${namespace}" 不匹配`,
          };
        }
        return { index, error: null };
      } catch {
        return { index, error: 'Token 验证请求失败，请检查网络' };
      }
    }),
  );

  bindingResults.forEach((result) => {
    if (result.error) errors[result.index] = result.error;
  });

  if (Object.keys(errors).length > 0) {
    return { success: false, errors };
  }

  try {
    const result = await precheckYuqueInit({
      yuqueUrls: entriesToValidate.map(({ repo }) => ({
        url: repo.url!.trim(),
        teamToken: repo.token!.trim(),
      })),
      ...(typeof options.maxDocsWarningThreshold === 'number'
        ? { maxDocsWarningThreshold: options.maxDocsWarningThreshold }
        : {}),
      allowTeamYuqueExpand: options.allowTeamYuqueExpand ?? false,
    });

    if (!result.success || !result.data) {
      mergeYuqueKbError(errors, entriesToValidate[0].index, result.errorMsg || '语雀知识库预检失败，请稍后重试');
    } else if (!result.data.passed) {
      const urlToIndex = new Map(entriesToValidate.map(({ repo, index }) => [normalizeYuqueUrl(repo.url), index]));
      const warnings = result.data.warnings || [];
      const formatWarning = options.formatWarning || formatDefaultYuquePrecheckWarning;

      if (warnings.length > 0) {
        warnings.forEach((warning) => {
          const index = resolveYuquePrecheckWarningIndex(
            warning,
            result.data?.sources,
            urlToIndex,
            entriesToValidate[0].index,
          );
          mergeYuqueKbError(errors, index, formatWarning(warning));
        });
      } else {
        mergeYuqueKbError(
          errors,
          entriesToValidate[0].index,
          `语雀知识库预检未通过，预计导入 ${result.data.estimatedDocCount} 篇文档`,
        );
      }
    }
  } catch {
    mergeYuqueKbError(errors, entriesToValidate[0].index, '语雀知识库预检请求失败，请检查网络');
  }

  return { success: Object.keys(errors).length === 0, errors };
}
