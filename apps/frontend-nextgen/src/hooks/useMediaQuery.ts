import { useEffect, useState } from 'react';

/**
 * 订阅一个 media query 并返回其当前是否匹配。
 *
 * SSR / 无 matchMedia 环境下安全降级：返回 false 且不订阅，避免 hydration 与 jsdom 报错。
 * 仅用于客户端运行时交互（如窄屏抽屉的「超过断点自动收起」），**不要**用于决定首屏可见性——
 * 首屏可见性应继续用 Tailwind 断点 class（`lg:flex` 等）控制，防 hydration 闪烁。
 */
function resolveMatches(query: string): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(query).matches;
}

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() => resolveMatches(query));

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mql = window.matchMedia(query);
    // query 变化或首次进入有 matchMedia 环境时，以真实值同步一次。
    setMatches(mql.matches);
    const handler = (event: MediaQueryListEvent) => setMatches(event.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, [query]);

  return matches;
}

/** `(min-width: ${px}px)` 的便捷封装。 */
export function useMinWidth(px: number): boolean {
  return useMediaQuery(`(min-width: ${px}px)`);
}
