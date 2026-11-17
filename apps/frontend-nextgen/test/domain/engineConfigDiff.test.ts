import {
  collapseDiffLines,
  computeLineDiff,
  countChangeBlocks,
} from '@/domain/engineConfigDiff';

const INDENT_2 = (line: string) => `  ${line}`;

describe('computeLineDiff', () => {
  it('识别修改行：removed 保留原行号，added 消耗新行号', () => {
    const oldText = ['{', INDENT_2('"a": 1,'), INDENT_2('"b": 2'), '}'].join('\n');
    const newText = ['{', INDENT_2('"a": 9,'), INDENT_2('"b": 2'), '}'].join('\n');

    const diff = computeLineDiff(oldText, newText);
    expect(diff).toEqual([
      { line: '{', type: 'unchanged', lineNumber: 1 },
      { line: INDENT_2('"a": 1,'), type: 'removed', lineNumber: 2 },
      { line: INDENT_2('"a": 9,'), type: 'added', lineNumber: 2 },
      { line: INDENT_2('"b": 2'), type: 'unchanged', lineNumber: 3 },
      { line: '}', type: 'unchanged', lineNumber: 4 },
    ]);
  });

  it('新增多行消耗连续行号，纯新增无 removed 行', () => {
    const diff = computeLineDiff('{\n}', '{\n  "x": 1\n}');
    expect(diff).toEqual([
      { line: '{', type: 'unchanged', lineNumber: 1 },
      { line: INDENT_2('"x": 1'), type: 'added', lineNumber: 2 },
      { line: '}', type: 'unchanged', lineNumber: 3 },
    ]);
  });

  it('文本相同时全部为 unchanged', () => {
    const diff = computeLineDiff('{"a":1}', '{"a":1}');
    expect(diff.every((d) => d.type === 'unchanged')).toBe(true);
    expect(diff.map((d) => d.lineNumber)).toEqual([1]);
  });
});

describe('collapseDiffLines', () => {
  const oldLines = Array.from({ length: 20 }, (_, i) => `line ${i}`);
  const newLines = [...oldLines];
  newLines[10] = 'line 10 changed';

  it('将上下文之外的未修改行折叠为 collapsed 块', () => {
    const fullDiff = computeLineDiff(oldLines.join('\n'), newLines.join('\n'));
    const collapsed = collapseDiffLines(fullDiff);

    const collapsedBlocks = collapsed.filter((item) => item.type === 'collapsed');
    expect(collapsedBlocks.length).toBeGreaterThan(0);
    // 变更行 ±3 上下文可见
    const visible = collapsed.filter((item): item is Extract<typeof item, { line: string }> => 'line' in item);
    expect(visible.some((item) => item.type === 'unchanged' && item.line === 'line 7')).toBe(true);
    expect(visible.some((item) => item.type === 'unchanged' && item.line === 'line 4')).toBe(false);
  });

  it('间隙小于 minCollapseLines 时直接显示不折叠', () => {
    const oldText = 'a\nb\nc\nd\ne';
    const fullDiff = computeLineDiff(oldText, oldText.replace('c', 'C'));
    const collapsed = collapseDiffLines(fullDiff, { minCollapseLines: 10 });
    expect(collapsed.filter((item) => item.type === 'collapsed')).toHaveLength(0);
    expect(collapsed.length).toBe(fullDiff.length);
  });

  it('无变更时原样返回', () => {
    const fullDiff = computeLineDiff('a\nb', 'a\nb');
    expect(collapseDiffLines(fullDiff)).toEqual(fullDiff);
  });
});

describe('countChangeBlocks', () => {
  it('统计连续 added/removed 块的数量', () => {
    const fullDiff = computeLineDiff(
      '{\n  "a": 1,\n  "b": 2\n}',
      '{\n  "a": 7,\n  "c": 3\n}',
    );
    // "a": 1→7 是一处，"b": 2→"c": 3 相邻算同一处连续修改块
    expect(countChangeBlocks(fullDiff)).toBe(1);

    const spaced = computeLineDiff(
      Array.from({ length: 12 }, (_, i) => `k${i}`).join('\n'),
      Array.from({ length: 12 }, (_, i) => (i === 2 || i === 11 ? `K${i}` : `k${i}`)).join('\n'),
    );
    expect(countChangeBlocks(spaced)).toBe(2);
  });
});
