/**
 * 响应式断点检测 Hook
 *
 * 移动端适配规则：
 * 1. 最小支持宽度 375px，小于 375px 页面出现横向滚动
 * 2. 大于 375px 使用 flex 自适应布局
 * 3. 移动端断点：768px
 */
import { useEffect, useState } from 'react';

export const BREAKPOINTS = {
  mobile: 768, // 移动端分界线
  tablet: 1024, // 平板分界线
  minWidth: 375, // 最小支持宽度（iPhone 标准宽度）
  tableMinWidth: 600, // 表格列表最小宽度（小于此宽度时固定并启用横向滚动）
} as const;

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    const media = window.matchMedia(query);
    const updateMatch = () => setMatches(media.matches);

    updateMatch();
    media.addEventListener('change', updateMatch);
    return () => media.removeEventListener('change', updateMatch);
  }, [query]);

  return matches;
}

/**
 * 是否是移动端（宽度 < 768px）
 */
export function useIsMobile(): boolean {
  return useMediaQuery(`(max-width: ${BREAKPOINTS.mobile - 1}px)`);
}

/**
 * 是否是平板（768px <= 宽度 < 1024px）
 */
export function useIsTablet(): boolean {
  return useMediaQuery(
    `(min-width: ${BREAKPOINTS.mobile}px) and (max-width: ${
      BREAKPOINTS.tablet - 1
    }px)`,
  );
}

/**
 * 获取当前布局模式
 * - mobile: < 768px
 * - tablet: 768px - 1024px
 * - desktop: > 1024px
 */
export function useLayoutMode(): 'mobile' | 'tablet' | 'desktop' {
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();

  if (isMobile) return 'mobile';
  if (isTablet) return 'tablet';
  return 'desktop';
}

/**
 * 是否小于最小支持宽度（375px）
 * 返回 true 时应该启用横向滚动
 */
export function useIsBelowMinWidth(): boolean {
  return useMediaQuery(`(max-width: ${BREAKPOINTS.minWidth - 1}px)`);
}

/**
 * 是否是窄屏（< tableMinWidth）
 * 用于表格类型列表页，小于此宽度时固定为 tableMinWidth 并启用横向滚动
 */
export function useIsNarrowScreen(): boolean {
  return useMediaQuery(`(max-width: ${BREAKPOINTS.tableMinWidth - 1}px)`);
}
