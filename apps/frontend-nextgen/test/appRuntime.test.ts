/** @jest-environment jsdom */
jest.mock('@umijs/max', () => ({ history: { replace: jest.fn() } }));

import { onRouteChange, qiankun } from '../src/app';

type RuntimeMetadataGlobal = {
  TEAMCLAW_MONACO_CDN_PREFIX?: string;
  TEAMCLAW_AIXCODING_MICRO_APP_PROD_ENTRY?: string;
  TEAMCLAW_AIXCODING_MICRO_APP_PROD_PUBLIC_PATH?: string;
};

const runtimeMetadataGlobal = globalThis as RuntimeMetadataGlobal;

type QiankunRuntimeConfig = {
  master?: {
    apps?: Array<{ name: string; entry: string; publicPath: string }>;
    excludeAssetFilter?: (assetUrl: string) => boolean;
  };
};

type GlobalWithMonacoPrefix = {
  TEAMCLAW_MONACO_CDN_PREFIX?: string;
};

const globalWithMonacoPrefix = globalThis as GlobalWithMonacoPrefix;
const MONACO_CDN_PREFIX =
  'https://gw.alipayobjects.com/os/lib/monaco-editor/0.45.0/min/vs';

afterEach(() => {
  if (globalWithMonacoPrefix.TEAMCLAW_MONACO_CDN_PREFIX !== undefined) {
    delete globalWithMonacoPrefix.TEAMCLAW_MONACO_CDN_PREFIX;
  }
  if (runtimeMetadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_ENTRY !== undefined) {
    delete runtimeMetadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_ENTRY;
  }
  if (runtimeMetadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_PUBLIC_PATH !== undefined) {
    delete runtimeMetadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_PUBLIC_PATH;
  }
  Reflect.deleteProperty(window, '__TERN__');
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

  test('Tern PROD runtime 注入 AIXCoding 子应用且保留 Monaco CDN 豁免', () => {
    const ternWindow = window as unknown as { __TERN__: { env: string } };
    ternWindow.__TERN__ = { env: 'PROD' };
    runtimeMetadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_ENTRY =
      'https://aixcoding.example/?bigfish_serve=1';
    runtimeMetadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_PUBLIC_PATH =
      'https://aixcoding.example/';
    globalWithMonacoPrefix.TEAMCLAW_MONACO_CDN_PREFIX = MONACO_CDN_PREFIX;

    const next = qiankun({ master: { apps: [] } }) as QiankunRuntimeConfig;
    expect(next.master?.apps).toHaveLength(1);
    expect(next.master?.apps?.[0]).toMatchObject({
      name: 'aixcoding',
      entry: 'https://aixcoding.example/?bigfish_serve=1',
      publicPath: 'https://aixcoding.example/',
    });
    expect(next.master?.excludeAssetFilter?.(`${MONACO_CDN_PREFIX}/loader.js`)).toBe(true);
  });
});
