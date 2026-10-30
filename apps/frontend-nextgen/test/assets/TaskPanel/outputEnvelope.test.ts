/** @jest-environment node */
// 输出信封剥离与可渲染源解析的边界分支。
import {
  asJsonCodeBlock,
  renderableSource,
  unwrapHttpEnvelope,
} from '@/assets/TaskPanel/outputEnvelope';
import { describe, expect, it } from '@jest/globals';

describe('outputEnvelope', () => {
  it('asJsonCodeBlock 只包装对象和数组', () => {
    expect(asJsonCodeBlock(null)).toBeNull();
    expect(asJsonCodeBlock(undefined)).toBeNull();
    expect(asJsonCodeBlock('text')).toBeNull();
    expect(asJsonCodeBlock([1, 2])).toBe('```json\n[\n  1,\n  2\n]\n```');

    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(asJsonCodeBlock(circular)).toBeNull();
  });

  it('unwrapHttpEnvelope 保留非信封对象和循环引用的载荷', () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(unwrapHttpEnvelope(circular)).toBe(circular);
    expect(unwrapHttpEnvelope({ custom: 1 })).toEqual({ custom: 1 });
    expect(unwrapHttpEnvelope({ result: null })).toBeNull();
  });

  it('renderableSource 支持空值、文本、代码块与 JSON 信封', () => {
    expect(renderableSource(null)).toBeNull();
    expect(renderableSource(undefined)).toBeNull();
    expect(renderableSource('')).toBeNull();
    expect(renderableSource('   ')).toBeNull();
    expect(renderableSource('普通 Markdown')).toBe('普通 Markdown');
    expect(renderableSource('```python\nprint("ok")\n```')).toBe('```python\nprint("ok")\n```');
    expect(renderableSource('```json\n{"result":"文本"}\n```')).toBe('文本');
    expect(renderableSource('{"code":0,"result":"文本"}')).toBe('文本');
  });

  it('对象输入可达字符串载荷或 JSON 代码块', () => {
    expect(renderableSource({ code: 0, result: '   ' })).toBeNull();
    expect(renderableSource({ code: 0, result: '完成' })).toBe('完成');
    expect(renderableSource({ code: 0, data: { value: 1 } })).toContain('"value": 1');
  });
});
