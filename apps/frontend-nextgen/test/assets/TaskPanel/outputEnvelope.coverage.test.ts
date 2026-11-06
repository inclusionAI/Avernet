/** @jest-environment node */
import { asJsonCodeBlock, renderableSource, unwrapHttpEnvelope } from '@/assets/TaskPanel/outputEnvelope';
import { describe, expect, it } from '@jest/globals';

// 未覆盖分支补充:asJsonCodeBlock 序列化异常兜底、``` 围栏内 JSON/非 JSON 的两种走向。

describe('asJsonCodeBlock', () => {
  it('环形引用等不可序列化对象 → 兜底 null(不外抛)', () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(asJsonCodeBlock(circular)).toBeNull();
    // renderableSource 对象链路上同样兜底为 null,由调用方回退原始内容
    expect(renderableSource(circular)).toBeNull();
  });

  it('非对象输入(字符串/数字/null)不产代码块', () => {
    expect(asJsonCodeBlock('str')).toBeNull();
    expect(asJsonCodeBlock(42)).toBeNull();
    expect(asJsonCodeBlock(null)).toBeNull();
  });
});

describe('renderableSource 围栏代码块', () => {
  it('```json 围栏内容是合法 JSON → 剥围栏后继续信封剥离', () => {
    const input = '```json\n{"success":true,"result":"围栏内信封载荷"}\n```';
    expect(renderableSource(input)).toBe('围栏内信封载荷');
  });

  it('``` 围栏内容非 JSON(如 Python 代码) → 原样保留围栏交 markdown 渲染', () => {
    const input = '```python\nprint("hello")\n```';
    expect(renderableSource(input)).toBe(input);
  });

  it('空字符串与空白输入 → null', () => {
    expect(renderableSource('')).toBeNull();
    expect(renderableSource('   ')).toBeNull();
    expect(renderableSource(null)).toBeNull();
  });

  it('无围栏的裸 JSON 字符串解析后信封剥离;普通文本原样返回', () => {
    expect(renderableSource('{"code":0,"data":{"result":"裸 JSON"}}')).toBe('裸 JSON');
    expect(renderableSource('普通 markdown 文本')).toBe('普通 markdown 文本');
  });
});

describe('unwrapHttpEnvelope 深度防兜底', () => {
  it('嵌套超过 5 层的纯 result 信封最多剥 5 层,防坏数据死循环', () => {
    let value: unknown = 'payload';
    for (let i = 0; i < 9; i += 1) {
      value = { result: value };
    }
    // 剥满 5 层后剩余仍是一个 {result: ...} 包装对象(深度上限兜底生效,未崩未死循环)
    const unwrapped = unwrapHttpEnvelope(value);
    expect(unwrapped).toMatchObject({ result: { result: expect.anything() } });
  });

  it('data 为非对象标量时不继续钻入', () => {
    // data=4 不是载荷对象 → 不视为可钻的信封层
    expect(unwrapHttpEnvelope({ code: 0, data: 4 })).toEqual({ code: 0, data: 4 });
  });
});
