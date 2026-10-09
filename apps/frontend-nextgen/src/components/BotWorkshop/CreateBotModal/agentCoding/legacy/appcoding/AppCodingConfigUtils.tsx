import type { YuquePrecheckWarning } from '@/services/botWorkshop/agentCodingLegacyService';
import React from 'react';
import type { CodeRepoItem } from '../aicoding/configFields';

export function isResourceSpecEnabledFromUrl(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    const value = new URLSearchParams(window.location.search).get('advanced');
    return value === '1' || value?.toLowerCase() === 'true';
  } catch {
    return false;
  }
}

export function isPositiveIntegerInput(value: string): boolean {
  const trimmed = value.trim();
  if (trimmed === '') return true;
  if (!/^\d+$/.test(trimmed)) return false;
  const n = Number(trimmed);
  return Number.isInteger(n) && n > 0;
}

export function findDuplicateRepoUrls(...repoLists: string[][]): Set<string> {
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  repoLists
    .flat()
    .map((repo) => repo.trim())
    .filter(Boolean)
    .forEach((url) => {
      if (seen.has(url)) duplicates.add(url);
      seen.add(url);
    });
  return duplicates;
}

export function toCodeRepoItems(repos: string[]): CodeRepoItem[] {
  return (repos.length ? repos : ['']).map((repo) => ({ repo_url: repo || '' }));
}

export function fromCodeRepoItems(items: CodeRepoItem[]): string[] {
  const next = items.map((item) => item.repo_url || '');
  return next.length ? next : [''];
}

export const MEMORY_OS_MANUAL_TITLE = '用 memoryOS 增强 Bot 长期记忆';
export const MEMORY_OS_MANUAL_URL = 'https://yuque.antfin.com/aixcoding/manual/memory-os#tUr8z';
const YUQUE_PRECHECK_SUPPORT_TIP = `如有疑问请查看《${MEMORY_OS_MANUAL_TITLE}》`;

export function formatYuquePrecheckWarning(warning: YuquePrecheckWarning): string {
  const appendTip = (message: string) => `${message}。${YUQUE_PRECHECK_SUPPORT_TIP}`;
  if (warning.message) return appendTip(warning.message);
  const messages: Record<string, string> = {
    team_url: '该 URL 是团队 URL，建议改为具体知识库 URL',
    too_many_docs: '预计导入文档数过多，建议拆成具体知识库分批导入',
    too_many_yuque_urls: '语雀链接数量过多，建议减少链接数量或分批导入',
    missing_token: '缺少语雀 Token，无法统计文档数',
    count_failed: '知识库文档数统计失败，请检查 Token 权限后重试',
    invalid_url: '语雀 URL 无法解析，请检查地址是否正确',
  };
  return appendTip(messages[warning.type] || '语雀知识库预检未通过，请检查后重试');
}

export function renderYuqueKbError(message: string): React.ReactNode {
  const titleStart = message.indexOf(`《${MEMORY_OS_MANUAL_TITLE}》`);
  if (titleStart < 0) return message;
  return (
    <>
      {message.slice(0, titleStart)}《
      <a
        href={MEMORY_OS_MANUAL_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="font-medium text-red-600 underline underline-offset-2 hover:text-red-700"
      >
        {MEMORY_OS_MANUAL_TITLE}
      </a>
      》{message.slice(titleStart + MEMORY_OS_MANUAL_TITLE.length + 2)}
    </>
  );
}
