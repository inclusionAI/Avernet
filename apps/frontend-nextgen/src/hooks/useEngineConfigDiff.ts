// 引擎配置 diff 计算：保存基线 vs 编辑草稿，展开状态由气泡消费方按块索引控制。
import {
  collapseDiffLines,
  computeLineDiff,
  type DiffItem,
  type DiffLine,
} from '@/domain/engineConfigDiff';
import { useCallback, useMemo, useState } from 'react';

export function useEngineConfigDiff(oldText: string, newText: string) {
  const [expandedBlocks, setExpandedBlocks] = useState<Set<number>>(new Set());

  const fullDiff = useMemo<DiffLine[] | null>(() => {
    if (oldText === newText) return null;
    return computeLineDiff(oldText, newText);
  }, [oldText, newText]);

  const collapsedDiff = useMemo<DiffItem[] | null>(() => {
    if (!fullDiff || fullDiff.length === 0) return null;
    return collapseDiffLines(fullDiff);
  }, [fullDiff]);

  const expandBlock = useCallback((blockIndex: number) => {
    setExpandedBlocks((prev) => new Set(prev).add(blockIndex));
  }, []);

  const resetExpandedBlocks = useCallback(() => {
    setExpandedBlocks(new Set());
  }, []);

  return { fullDiff, collapsedDiff, expandedBlocks, expandBlock, resetExpandedBlocks };
}
