import { defaultCapabilities } from '@/capabilities/defaultCapabilities';
import { extendCapabilities } from '@/capabilities';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

/**
 * SPM 埋点基建（add-spm-tracking）：
 * facade 委托 capabilities.trackerAdapter；Open Core 默认 no-op；埋点异常与业务完全隔离。
 * 码值防泄漏见 test/tracking/points.test.ts。
 */

// 递归收集 Points 全部叶子值
function collectPointValues(obj: unknown): string[] {
  if (typeof obj === 'string') return [obj];
  if (obj && typeof obj === 'object') {
    return Object.values(obj).flatMap(collectPointValues);
  }
  return [];
}

describe('trackerAdapter 默认 no-op（Open Core）', () => {
  const origTracert = (globalThis as Record<string, unknown>).Tracert;

  afterEach(() => {
    (globalThis as Record<string, unknown>).Tracert = origTracert;
  });

  it('click/expo/config 均为空操作，不抛错、不触达任何全局 SDK', () => {
    const spy = jest.fn();
    (globalThis as Record<string, unknown>).Tracert = spy;
    expect(() => {
      defaultCapabilities.trackerAdapter.click('任意点位', { bot_id: 'x' });
      defaultCapabilities.trackerAdapter.expo('任意点位');
      defaultCapabilities.trackerAdapter.config({ spmBPos: 'y' });
    }).not.toThrow();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('Tracert facade 委托与异常隔离', () => {
  const sent: Array<{ method: string; pointKey?: string; args: unknown[] }> = [];

  beforeAll(() => {
    extendCapabilities({
      trackerAdapter: {
        click: (pointKey, params) => void sent.push({ method: 'click', pointKey, args: [params] }),
        expo: (pointKey, params) => void sent.push({ method: 'expo', pointKey, args: [params] }),
        config: (options) => void sent.push({ method: 'config', args: [options] }),
      },
    });
  });

  it('click/expo 透传语义点位常量与扩展参数', () => {
    const pointKeys = collectPointValues(Points);
    const key = pointKeys[0] ?? '骨架阶段占位点位名';
    Tracert.click(key, { bot_id: 'b1' });
    Tracert.expo(key);
    expect(sent).toEqual([
      { method: 'click', pointKey: key, args: [{ bot_id: 'b1' }] },
      { method: 'expo', pointKey: key, args: [undefined] },
    ]);
  });

  it('config 透传运行期配置', () => {
    Tracert.config({ spmBPos: 'demo' });
    expect(sent.at(-1)).toEqual({ method: 'config', args: [{ spmBPos: 'demo' }] });
  });

  it('adapter 抛错时 facade 捕获降级，不向业务抛出', () => {
    extendCapabilities({
      trackerAdapter: {
        click: () => {
          throw new Error('sdk not ready');
        },
        expo: () => {
          throw new Error('sdk not ready');
        },
        config: () => {
          throw new Error('sdk not ready');
        },
      },
    });
    expect(() => {
      Tracert.click('任意点位');
      Tracert.expo('任意点位');
      Tracert.config({});
    }).not.toThrow();
  });
});
