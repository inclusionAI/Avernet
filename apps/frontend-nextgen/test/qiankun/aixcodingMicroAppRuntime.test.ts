/** @jest-environment jsdom */
import { beforeEach, describe, expect, it } from '@jest/globals';
import {
  applyAixcodingMicroAppRuntimeConfig,
  getRuntimeAixcodingMicroApp,
  type QiankunMasterApp,
  type QiankunRuntimeConfig,
} from '../../src/qiankun/aixcodingMicroAppRuntime';

type MetadataGlobal = {
  TEAMCLAW_AIXCODING_MICRO_APP_PRE_ENTRY?: string;
  TEAMCLAW_AIXCODING_MICRO_APP_PRE_PUBLIC_PATH?: string;
  TEAMCLAW_AIXCODING_MICRO_APP_PROD_ENTRY?: string;
  TEAMCLAW_AIXCODING_MICRO_APP_PROD_PUBLIC_PATH?: string;
};

const metadataGlobal = globalThis as MetadataGlobal;

const setTernEnv = (env?: string, localDev = false) => {
  const target = window as unknown as {
    __TERN__?: { env?: string; _localDev?: boolean };
  };
  if (env === undefined) {
    Reflect.deleteProperty(target, '__TERN__');
    return;
  }
  target.__TERN__ = { env, _localDev: localDev };
};

beforeEach(() => {
  delete metadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PRE_ENTRY;
  delete metadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PRE_PUBLIC_PATH;
  delete metadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_ENTRY;
  delete metadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_PUBLIC_PATH;
  setTernEnv(undefined);
});

describe('AIXCoding qiankun runtime metadata', () => {
  it('stays no-op when the closed overlay does not inject deployment metadata', () => {
    setTernEnv('PRE');
    const config = { master: { apps: [] } };

    expect(getRuntimeAixcodingMicroApp()).toBeNull();
    expect(applyAixcodingMicroAppRuntimeConfig(config)).toBe(config);
  });

  it('sets the runtime public path from props when MicroApp strips the top-level value', () => {
    const config: QiankunRuntimeConfig = {
      master: {
        apps: [
          {
            name: 'aixcoding',
            entry: 'https://test.pre.alipay.com:8000/?bigfish_serve=1',
            // This mirrors the generated MicroApp component input, which only
            // preserves name/entry/container/props when calling loadMicroApp.
            props: { publicPath: 'https://test.pre.alipay.com:8000/' },
          },
        ],
      },
    };

    const next = applyAixcodingMicroAppRuntimeConfig(config);
    const globalContext: Record<string, unknown> = {};
    const app = next.master?.apps?.find((item) => item.name === 'aixcoding');
    const beforeLoad = next.master?.lifeCycles?.beforeLoad;
    if (Array.isArray(beforeLoad)) {
      beforeLoad[0]?.(app as QiankunMasterApp, globalContext);
    } else {
      beforeLoad?.(app as QiankunMasterApp, globalContext);
    }

    expect(globalContext.publicPath).toBe('https://test.pre.alipay.com:8000/');
    expect(globalContext.TURBOPACK_CHUNK_BASE_PATH).toBe(
      'https://test.pre.alipay.com:8000/',
    );
  });

  it('falls back to the public path injected by qiankun', () => {
    const config: QiankunRuntimeConfig = {
      master: {
        apps: [
          {
            name: 'aixcoding',
            entry: 'https://test.pre.alipay.com:8000/?bigfish_serve=1',
          },
        ],
      },
    };

    const next = applyAixcodingMicroAppRuntimeConfig(config);
    const globalContext: Record<string, unknown> = {
      __INJECTED_PUBLIC_PATH_BY_QIANKUN__: 'https://test.pre.alipay.com:8000/',
    };
    const app = next.master?.apps?.find((item) => item.name === 'aixcoding');
    const beforeLoad = next.master?.lifeCycles?.beforeLoad;
    if (Array.isArray(beforeLoad)) {
      beforeLoad[0]?.(app as QiankunMasterApp, globalContext);
    } else {
      beforeLoad?.(app as QiankunMasterApp, globalContext);
    }

    expect(globalContext.publicPath).toBe('https://test.pre.alipay.com:8000/');
    expect(globalContext.TURBOPACK_CHUNK_BASE_PATH).toBe(
      'https://test.pre.alipay.com:8000/',
    );
  });

  it('registers the PRE deployment app after Tern publishes its environment', () => {
    metadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PRE_ENTRY =
      'https://aixcoding-pre.example/?bigfish_serve=1';
    metadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PRE_PUBLIC_PATH =
      'https://aixcoding-pre.example/';
    setTernEnv('PRE');

    const config: QiankunRuntimeConfig = { master: { apps: [] } };
    const next = applyAixcodingMicroAppRuntimeConfig(config);

    expect(next.master?.apps).toHaveLength(1);
    expect(next.master?.apps?.[0]).toMatchObject({
      name: 'aixcoding',
      entry: 'https://aixcoding-pre.example/?bigfish_serve=1',
      publicPath: 'https://aixcoding-pre.example/',
      credentials: true,
    });
    expect(next.master?.lifeCycles?.beforeLoad).toHaveLength(1);
  });

  it('replaces the queued app with the PROD deployment app', () => {
    metadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_ENTRY =
      'https://aixcoding.example/?bigfish_serve=1';
    metadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_PUBLIC_PATH =
      'https://aixcoding.example/';
    setTernEnv('PROD');

    const existing = {
      name: 'aixcoding',
      entry: 'https://wrong-host.example/',
      publicPath: 'https://wrong-host.example/',
      credentials: true,
    };
    const next = applyAixcodingMicroAppRuntimeConfig({
      master: { apps: [existing] },
    });

    expect(next.master?.apps).toHaveLength(1);
    expect(next.master?.apps?.[0]).toMatchObject({
      name: 'aixcoding',
      entry: 'https://aixcoding.example/?bigfish_serve=1',
      publicPath: 'https://aixcoding.example/',
      credentials: true,
    });
    expect(next.master?.lifeCycles?.beforeLoad).toHaveLength(1);
  });

  it('does not rewrite explicit local qiankun metadata in Tern local dev', () => {
    metadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_ENTRY =
      'https://aixcoding.example/?bigfish_serve=1';
    metadataGlobal.TEAMCLAW_AIXCODING_MICRO_APP_PROD_PUBLIC_PATH =
      'https://aixcoding.example/';
    setTernEnv('PROD', true);

    const config = {
      master: {
        apps: [
          {
            name: 'aixcoding',
            entry: 'https://test.pre.alipay.com:8000/?bigfish_serve=1',
            publicPath: 'https://test.pre.alipay.com:8000/',
          },
        ],
      },
    };

    const next = applyAixcodingMicroAppRuntimeConfig(config);

    expect(next.master?.apps).toEqual(config.master?.apps);
    expect(next.master?.lifeCycles?.beforeLoad).toHaveLength(1);

    const globalContext: Record<string, unknown> = {};
    const app = next.master?.apps?.find((item) => item.name === 'aixcoding');
    const beforeLoad = next.master?.lifeCycles?.beforeLoad;
    if (Array.isArray(beforeLoad)) {
      beforeLoad[0]?.(app as QiankunMasterApp, globalContext);
    } else {
      beforeLoad?.(app as QiankunMasterApp, globalContext);
    }

    expect(globalContext.publicPath).toBe('https://test.pre.alipay.com:8000/');
    expect(globalContext.TURBOPACK_CHUNK_BASE_PATH).toBe(
      'https://test.pre.alipay.com:8000/',
    );
  });
});
