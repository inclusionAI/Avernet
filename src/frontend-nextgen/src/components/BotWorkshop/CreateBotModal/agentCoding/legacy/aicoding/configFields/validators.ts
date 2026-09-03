const validateUrlPrefix = (url: string, prefix: string) =>
  !(url.startsWith('https://') && (prefix !== 'yuque' || /yuque\./i.test(url)));
import type { CodeRepoItem, YuqueKbRepoItem } from './types';

export function normalizeCodeRepoItems(value?: CodeRepoItem[] | null): CodeRepoItem[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => ({ repo_url: item?.repo_url || '' }));
}

export function normalizeYuqueKbRepoItems(value?: YuqueKbRepoItem[] | null): YuqueKbRepoItem[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => ({
    url: item?.url || '',
    token: item?.token || '',
  }));
}

export function compactCodeRepoItems(value?: CodeRepoItem[] | null): CodeRepoItem[] {
  return normalizeCodeRepoItems(value)
    .map((item) => ({ repo_url: item.repo_url?.trim() || '' }))
    .filter((item) => !!item.repo_url);
}

export function compactYuqueKbRepoItems(value?: YuqueKbRepoItem[] | null): YuqueKbRepoItem[] {
  return normalizeYuqueKbRepoItems(value)
    .map((item) => ({
      url: item.url?.trim() || '',
      token: item.token?.trim() || undefined,
    }))
    .filter((item) => !!item.url);
}

export function findDuplicateCodeRepoUrls(...repoGroups: Array<CodeRepoItem[] | string[] | undefined>): Set<string> {
  const allUrls = repoGroups.reduce<string[]>((urls, group) => {
    for (const item of group || []) {
      const url = (typeof item === 'string' ? item : item.repo_url || '').trim();
      if (url) urls.push(url);
    }
    return urls;
  }, []);
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  for (const url of allUrls) {
    if (seen.has(url)) duplicates.add(url);
    seen.add(url);
  }
  return duplicates;
}

export function validateCodeRepoItems(
  label: string,
  value: CodeRepoItem[] | undefined,
  options?: { required?: boolean },
): string | null {
  const items = normalizeCodeRepoItems(value);
  const validItems = compactCodeRepoItems(items);
  if (options?.required && validItems.length === 0) return `请填写${label}`;

  const duplicates = findDuplicateCodeRepoUrls(items);
  if (duplicates.size > 0) {
    return `${label} 存在重复的仓库地址：${Array.from(duplicates).join('、')}`;
  }
  return null;
}

export function isValidYuqueUrl(url: string): boolean {
  return validateUrlPrefix(url, 'yuque');
}

export function validateYuqueKbRepoItems(
  label: string,
  value: YuqueKbRepoItem[] | undefined,
  options?: { required?: boolean; requireTokenForFilledUrl?: boolean },
): string | null {
  const items = normalizeYuqueKbRepoItems(value);
  const validItems = compactYuqueKbRepoItems(items);
  if (options?.required && validItems.length === 0) return `请填写${label}`;

  for (const item of items) {
    const url = item.url?.trim() || '';
    if (!url) continue;
    if (!isValidYuqueUrl(url)) return `${label} 需填写合法的语雀知识库地址`;
    if (options?.requireTokenForFilledUrl && !item.token?.trim()) {
      return '填写了语雀知识库地址时，Token 为必填项';
    }
  }
  return null;
}
