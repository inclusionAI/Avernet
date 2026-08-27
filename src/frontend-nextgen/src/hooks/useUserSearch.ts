// 员工目录搜索 Hook：封装 getUserSearchCapability + 防抖 + 状态。
//
// 对齐 useHumanIdentity「纯读 capability」模式：组件只消费 results/loading/error/supported，
// 不直接 fetch / import service（import-boundaries 门禁）。
// Open Core 默认 capability=null → supported=false，UserSearchDropdown 降级为手填工号。
// 内部 overlay（src/extensions/internal.ts）注入 antbuservice 实现，本 hook 无感。
// Hook ≤ 150 行（AGENTS.md 约束）。
import { getCapabilities, type SearchedUser } from '@/capabilities';
import { useEffect, useState } from 'react';

export interface UseUserSearchOptions {
  /** 触发搜索的最小字符数（默认 2，对齐 open-claw UserSearchDropdown） */
  minLength?: number;
  /** 防抖毫秒（默认 300） */
  debounceMs?: number;
}

export interface UseUserSearchResult {
  results: SearchedUser[];
  loading: boolean;
  error: string | null;
  /** 当前运行环境是否支持员工目录搜索（Open Core=false → 降级手填工号） */
  supported: boolean;
}

/**
 * 按关键词搜索员工目录。防抖后调 capability.search；空/过短/不支持 → 空结果不请求。
 * capability.search 在内部 overlay 指向稳定的模块级 searchUsersByAntbu，
 * 故 searchFn 引用稳定，effect 依赖不随每次渲染抖动。
 */
export function useUserSearch(keyword: string, options?: UseUserSearchOptions): UseUserSearchResult {
  const minLength = options?.minLength ?? 2;
  const debounceMs = options?.debounceMs ?? 300;

  const cap = getCapabilities().getUserSearchCapability();
  const supported = cap.status === 'available' && !!cap.value;
  const searchFn = cap.value?.search;

  const [results, setResults] = useState<SearchedUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = keyword.trim();

  useEffect(() => {
    if (!supported || !searchFn || trimmed.length < minLength) {
      setResults([]);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const list = await searchFn(trimmed);
        if (cancelled) return;
        setResults(list);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        console.warn('[useUserSearch] 搜索失败', e);
        setResults([]);
        setError('搜索失败，请重试');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, debounceMs);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // searchFn 引用稳定（内部 overlay 指向模块级函数 / Open Core 为 undefined），可安全入依赖。
  }, [trimmed, minLength, debounceMs, supported, searchFn]);

  return { results, loading, error, supported };
}
