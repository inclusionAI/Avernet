// 引擎配置 diff 纯逻辑：行级 LCS diff + 未修改块折叠。
// 输出结构与旧版 EngineConfigDrawer 的 useDiffCollapse 保持一致，供保存弹层按行渲染。

export interface DiffLine {
  line: string;
  type: 'added' | 'removed' | 'unchanged';
  lineNumber: number;
}

export interface CollapsedBlock {
  type: 'collapsed';
  count: number;
  startLine: number;
  endLine: number;
}

export type DiffItem = DiffLine | CollapsedBlock;

function splitLines(text: string): string[] {
  if (text === '') return [];
  const lines = text.split('\n');
  return text.endsWith('\n') ? lines.slice(0, -1) : lines;
}

/** LCS 系数矩阵超过该规模时退化为“全部删除 + 全部新增”，避免大文档 O(n*m) 内存膨胀。 */
const MAX_LCS_CELLS = 4_000_000;

/**
 * 行级 diff（LCS）。lineNumber 对齐“新版”行号；removed 行不消耗行号（与旧版实现一致），
 * 折叠块的 startLine/endLine 直接取自该行号。
 */
export function computeLineDiff(oldText: string, newText: string): DiffLine[] {
  const oldLines = splitLines(oldText);
  const newLines = splitLines(newText);
  const n = oldLines.length;
  const m = newLines.length;

  const result: DiffLine[] = [];
  let lineNumber = 0;

  if (n * m > MAX_LCS_CELLS) {
    for (let i = 0; i < n; i++) result.push({ line: oldLines[i], type: 'removed', lineNumber: lineNumber + 1 });
    for (let j = 0; j < m; j++) result.push({ line: newLines[j], type: 'added', lineNumber: ++lineNumber });
    return result;
  }

  // dp[i][j] = oldLines[i:] 与 newLines[j:] 的 LCS 长度
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = oldLines[i] === newLines[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (oldLines[i] === newLines[j]) {
      result.push({ line: oldLines[i], type: 'unchanged', lineNumber: ++lineNumber });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      result.push({ line: oldLines[i], type: 'removed', lineNumber: lineNumber + 1 });
      i++;
    } else {
      result.push({ line: newLines[j], type: 'added', lineNumber: ++lineNumber });
      j++;
    }
  }
  while (i < n) {
    result.push({ line: oldLines[i], type: 'removed', lineNumber: lineNumber + 1 });
    i++;
  }
  while (j < m) {
    result.push({ line: newLines[j], type: 'added', lineNumber: ++lineNumber });
    j++;
  }
  return result;
}

export interface CollapseOptions {
  /** 变更行前后保留的上下文行数 */
  contextLines?: number;
  /** 最少折叠行数：间隙小于该值时直接显示，不折叠 */
  minCollapseLines?: number;
}

/**
 * 折叠未修改行：变更行 ± contextLines 保留，其余间隙压缩为 CollapsedBlock。
 * 未发生变化时返回原内容（交由调用方决定空态展示）。
 */
export function collapseDiffLines(
  fullDiff: DiffLine[],
  { contextLines = 3, minCollapseLines = 5 }: CollapseOptions = {},
): DiffItem[] {
  if (fullDiff.length === 0) return [];

  const changeIndexes = fullDiff.map((item, idx) => (item.type !== 'unchanged' ? idx : -1)).filter((idx) => idx !== -1);
  if (changeIndexes.length === 0) return fullDiff;

  // 变更行 ± contextLines 的显示范围，合并重叠区间
  const showRanges: Array<[number, number]> = changeIndexes.map((idx) => [
    Math.max(0, idx - contextLines),
    Math.min(fullDiff.length - 1, idx + contextLines),
  ]);
  showRanges.sort((a, b) => a[0] - b[0]);
  const mergedRanges: Array<[number, number]> = [];
  let current = showRanges[0];
  for (let k = 1; k < showRanges.length; k++) {
    const [start, end] = showRanges[k];
    if (start <= current[1] + 1) {
      current[1] = Math.max(current[1], end);
    } else {
      mergedRanges.push(current);
      current = [start, end];
    }
  }
  mergedRanges.push(current);

  // 间隙 >= minCollapseLines 时折叠，否则原样显示
  const result: DiffItem[] = [];
  let currentPos = 0;
  for (const [start, end] of mergedRanges) {
    if (currentPos < start) {
      const gap = start - currentPos;
      if (gap >= minCollapseLines) {
        result.push({
          type: 'collapsed',
          count: gap,
          startLine: fullDiff[currentPos].lineNumber,
          endLine: fullDiff[start - 1].lineNumber,
        });
      } else {
        for (let k = currentPos; k < start; k++) result.push(fullDiff[k]);
      }
    }
    for (let k = start; k <= end; k++) result.push(fullDiff[k]);
    currentPos = end + 1;
  }

  // 末尾剩余行
  if (currentPos < fullDiff.length) {
    const remaining = fullDiff.length - currentPos;
    if (remaining >= minCollapseLines) {
      result.push({
        type: 'collapsed',
        count: remaining,
        startLine: fullDiff[currentPos].lineNumber,
        endLine: fullDiff[fullDiff.length - 1].lineNumber,
      });
    } else {
      for (let k = currentPos; k < fullDiff.length; k++) result.push(fullDiff[k]);
    }
  }

  return result;
}

/** 统计连续 added/removed 块的数量（旧版“N 处修改”口径）。 */
export function countChangeBlocks(fullDiff: DiffLine[]): number {
  let count = 0;
  let inChangeBlock = false;
  for (const item of fullDiff) {
    if (item.type === 'added' || item.type === 'removed') {
      if (!inChangeBlock) {
        count++;
        inChangeBlock = true;
      }
    } else {
      inChangeBlock = false;
    }
  }
  return count;
}
