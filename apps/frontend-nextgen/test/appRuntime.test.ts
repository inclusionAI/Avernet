jest.mock('@umijs/max', () => ({ history: { replace: jest.fn() } }));

import { onRouteChange, qiankun } from '../src/app';

type QiankunRuntimeConfig = {
  master?: {
    excludeAssetFilter?: (assetUrl: string) => boolean;
  };
};

type GlobalWithMonacoPrefix = typeof globalThis & {
  TEAMCLAW_MONACO_CDN_PREFIX?: string;
};

const globalWithMonacoPrefix = globalThis as GlobalWithMonacoPrefix;
const MONACO_CDN_PREFIX =
  'https://gw.alipayobjects.com/os/lib/monaco-editor/0.45.0/min/vs';

afterEach(() => {
  if (globalWithMonacoPrefix.TEAMCLAW_MONACO_CDN_PREFIX === undefined) return;
  delete globalWithMonacoPrefix.TEAMCLAW_MONACO_CDN_PREFIX;
});

describe('app runtime', () => {
  test('Open Core 默认路由守卫不跳转', () => {
    expect(() => onRouteChange({ location: { pathname: '/workspace' } })).not.toThrow();
  });

  test('Internal 构建给 qiankun 注入 Monaco CDN 原生加载豁免', () => {
    globalWithMonacoPrefix.TEAMCLAW_MONACO_CDN_PREFIX = MONACO_CDN_PREFIX;

    const next = qiankun({}) as QiankunRuntimeConfig;
    expect(next.master?.excludeAssetFilter).toBeInstanceOf(Function);
    expect(next.master?.excludeAssetFilter?.(`${MONACO_CDN_PREFIX}/loader.js`)).toBe(true);
    expect(
      next.master?.excludeAssetFilter?.(`${MONACO_CDN_PREFIX}/language/jsonMode.js`),
    ).toBe(true);
    expect(next.master?.excludeAssetFilter?.('https://example.com/loader.js')).toBe(false);
  });

  test('Open Core 未注入前缀时不改写 qiankun runtime 配置', () => {
    const config = { master: { excludeAssetFilter: undefined } };
    expect(qiankun(config)).toBe(config);
  });
});
