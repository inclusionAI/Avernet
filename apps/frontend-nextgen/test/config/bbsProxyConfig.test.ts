import { readFileSync } from 'node:fs';
import { join } from 'node:path';

describe('BBS proxy routing', () => {
  const originalEnv = { ...process.env };

  afterEach(() => {
    process.env = { ...originalEnv };
    jest.resetModules();
  });

  test('development proxy sends BBS routes to Gateway before the generic task upstream', () => {
    process.env.PRESET = 'pre';
    process.env.TEAMCLAW_GW_BASE = 'https://gateway.example.test';
    process.env.TASK_ENGINE_UPSTREAM = 'https://task-engine.example.test';
    jest.resetModules();

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const config = require('../../config/config.local').default;
    const proxy = config.proxy as Record<string, { target: string }>;
    const keys = Object.keys(proxy);

    expect(proxy['/openapi/v1/collaboration/tasks/bbs'].target).toBe('https://gateway.example.test');
    expect(proxy['/openapi/v1/collaboration/tasks/bots/*/bbs/**'].target).toBe('https://gateway.example.test');
    expect(proxy['/openapi/v1/collaboration/tasks'].target).toBe('https://task-engine.example.test');
    expect(proxy['/api/v1/collaboration/tasks'].target).toBe('https://task-engine.example.test');
    expect(keys.indexOf('/openapi/v1/collaboration/tasks/bbs')).toBeLessThan(
      keys.indexOf('/openapi/v1/collaboration/tasks'),
    );
    expect(keys.indexOf('/api/v1/collaboration/tasks')).toBeLessThan(keys.indexOf('/api/v1/collaboration'));
  });

  test('development config accepts the internal task API path for direct Engine debugging', () => {
    process.env.TEAMCLAW_TASK_API_BASE = '/api/v1/collaboration/tasks';
    jest.resetModules();

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const config = require('../../config/config.local').default;

    expect(config.define.TEAMCLAW_TASK_API_BASE).toBe('/api/v1/collaboration/tasks');
  });

  test('deployment Nginx sends public BBS routes to the Gateway upstream', () => {
    const template = readFileSync(join(process.cwd(), 'deploy/nginx/default.conf.template'), 'utf8');

    expect(template).toContain(
      'location /openapi/v1/collaboration/tasks/bbs/ { proxy_pass ${TEAMCLAW_GATEWAY_UPSTREAM};',
    );
    expect(template).toContain(
      'location ~ ^/openapi/v1/collaboration/tasks/bots/[^/]+/bbs(?:/|$) { proxy_pass ${TEAMCLAW_GATEWAY_UPSTREAM};',
    );
  });
});

describe('BCS-only development profile (FRONTEND_API_PROFILE=bcs-only)', () => {
  const originalEnv = { ...process.env };

  afterEach(() => {
    process.env = { ...originalEnv };
    jest.resetModules();
  });

  test('compiles the backend switch off and routes the whole BCS collaboration subtree', () => {
    process.env.FRONTEND_API_PROFILE = 'bcs-only';
    const bcsUpstream = 'http://127.0.0.1:22100';
    process.env.BCS_UPSTREAM = bcsUpstream;
    jest.resetModules();

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const config = require('../../config/config.local').default;
    const proxy = config.proxy as Record<string, { target: string; ws?: boolean }>;
    const keys = Object.keys(proxy);

    // 与部署 runtime-config.js 同语义：bcs-only 编译关闭可选 Backend 增强。
    expect(config.define.TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED).toBe(false);
    // 与 prod 模板对齐：整棵 /openapi/v1/collaboration 子树去 BCS（含 tasks 等
    // 该子树下的一切路径），而不是只覆写好友发现用到的三个窄前缀。
    expect(proxy['/openapi/v1/collaboration'].target).toBe(bcsUpstream);
    // 同源群聊 WS（messages/ws、group/ws）在 dev 也必须带 ws:true。
    expect(proxy['/openapi/v1/collaboration'].ws).toBe(true);
    // 内部候选搜索必须先于 /api 通配与 /api/v1/collaboration（umi 按 key 插入顺序匹配）。
    expect(proxy['/api/v1/collaboration/bots/*/candidates/search'].target).toBe(bcsUpstream);
    const searchIndex = keys.indexOf('/api/v1/collaboration/bots/*/candidates/search');
    expect(searchIndex).toBeGreaterThanOrEqual(0);
    expect(searchIndex).toBeLessThan(keys.indexOf('/api/v1/collaboration'));
    expect(searchIndex).toBeLessThan(keys.indexOf('/api'));
    // openapi 侧 BCS 子树同样先于通用 /openapi gateway 键。
    expect(keys.indexOf('/openapi/v1/collaboration')).toBeLessThan(keys.indexOf('/openapi'));
    // 反向：Admin 面的 bots / bots/spaces 在 dev 也不落到 BCS（与 prod 的 /openapi/ 404 对齐）。
    expect(proxy['/openapi/v1/bots/spaces'].target).not.toBe(bcsUpstream);
    expect(proxy['/openapi/v1/bots/work-orders'].target).not.toBe(bcsUpstream);
  });

  test('full profile leaves the compile-time switch and proxy map untouched', () => {
    // FRONTEND_API_PROFILE 缺省 = full：不新增 BCS 开发代理，也不覆盖编译开关
    //（有效 define 仍取 config/config.ts 的默认 true 逻辑）。
    jest.resetModules();

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const config = require('../../config/config.local').default;
    const proxy = config.proxy as Record<string, { target: string }>;

    expect(proxy['/openapi/v1/collaboration']).toBeUndefined();
    expect(proxy['/openapi/v1/collaboration/bots/*/candidates']).toBeUndefined();
    expect(proxy['/api/v1/collaboration/bots/*/candidates/search']).toBeUndefined();
    expect(config.define.TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED).toBeUndefined();
  });

  test('rejects unknown FRONTEND_API_PROFILE values', () => {
    process.env.FRONTEND_API_PROFILE = 'staging';
    jest.resetModules();

    expect(() => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      require('../../config/config.local');
    }).toThrow(/Unknown FRONTEND_API_PROFILE: staging/);
  });
});
