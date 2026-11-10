/**
 * BCS-only 同源代理模板契约（bcs-standalone-friend-discovery Task 5）。
 *
 * 只读文本断言与 full 模板同法：部署面单文件维护；实际转发行为由官方 Nginx
 * entrypoint 的标准 ${...} 变量替换 + docs/bcs-only-friend-discovery-validation.md
 * 的人工浏览器验收背书。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

describe('BCS-only same-origin nginx profile', () => {
  const bcsOnlyConfig = readFileSync(
    join(process.cwd(), 'deploy/nginx/default.bcs-only.conf.template'),
    'utf8',
  );

  test('proxies exactly the BCS friend-discovery roots to a single BCS upstream', () => {
    expect(bcsOnlyConfig).toContain('${BCS_UPSTREAM}');
    // BCS OAuth：浏览器登录跳转与 /auth/callback/<provider> 回调必须同源可达。
    expect(bcsOnlyConfig).toContain('location /auth/ { proxy_pass ${BCS_UPSTREAM};');
    // BCS 公开 auth 面（/openapi/v1/auth/url|user|refresh|logout）。
    expect(bcsOnlyConfig).toContain('location /openapi/v1/auth/ { proxy_pass ${BCS_UPSTREAM};');
    // 好友发现协作子树：整棵 /openapi/v1/collaboration/ 归 BCS（BCS-only 下 BCS 是唯一
    // 业务 API），与 dev profile 的整棵覆写一致。
    expect(bcsOnlyConfig).toContain('location /openapi/v1/collaboration/ { proxy_pass ${BCS_UPSTREAM};');
    // BCS 内部语义候选搜索（智能搜索）精确正则；正则 location 优先于前缀 location。
    expect(bcsOnlyConfig).toContain(
      'location ~ ^/api/v1/collaboration/bots/[^/]+/candidates/search(?:/|$) { proxy_pass ${BCS_UPSTREAM};',
    );
  });

  test('keeps same-origin group-chat WebSocket upgrades on the collaboration subtree', () => {
    // 群聊 WS（/openapi/v1/collaboration/messages/ws、/group/ws）经同一 location
    // 转发；缺 Upgrade/Connection 头会握手失败。写法与 full 模板的 WS location 一致：
    // $http_upgrade 为空时 nginx 不下发 Upgrade 头，普通 HTTP 请求不受影响。
    const collaborationLocations = bcsOnlyConfig
      .split('\n')
      .filter((line) => line.includes('location /openapi/v1/collaboration/'));

    expect(collaborationLocations.length).toBeGreaterThan(0);
    for (const location of collaborationLocations) {
      expect(location).toContain('proxy_http_version 1.1;');
      expect(location).toContain('proxy_set_header Upgrade $http_upgrade;');
      expect(location).toContain('proxy_set_header Connection "upgrade";');
    }
  });

  test('does not forward admin/gateway owned routes to BCS', () => {
    // 反向约束：/openapi/v1/bots、/openapi/v1/bots/spaces 等在 full 模板里属于
    // Admin/Gateway，bcs-only 不得把它们落到 BCS，而是由 /openapi/ 显式 404。
    const locationLines = bcsOnlyConfig
      .split('\n')
      .filter((line) => line.trim().startsWith('location'));

    expect(locationLines.some((line) => line.includes('/openapi/v1/bots'))).toBe(false);
    expect(bcsOnlyConfig).not.toContain('TEAMCLAW_ADMIN_UPSTREAM');
    // 整棵 collaboration 子树归 BCS 后，不再为 tasks / friend-connections 另立更窄规则。
    expect(locationLines.some((line) => line.includes('/openapi/v1/collaboration/tasks'))).toBe(false);
    expect(locationLines.some((line) => line.includes('/collaboration/friend-connections'))).toBe(false);
  });

  test('does not reference any full-stack upstream variable', () => {
    for (const forbidden of [
      'BCS_AUTH_UPSTREAM',
      'TASK_ENGINE_UPSTREAM',
      'TEAMCLAW_GATEWAY_UPSTREAM',
      'TEAMCLAW_ADMIN_UPSTREAM',
      'PRIVATE_CHAT_MANAGEMENT_UPSTREAM',
      'PRIVATE_CHAT_SESSION_UPSTREAM',
      'CLAWWEB_UPSTREAM',
      'AIXCORE_UPSTREAM',
    ]) {
      expect(bcsOnlyConfig).not.toContain(forbidden);
    }
  });

  test('returns explicit 404 for unmatched /api/ and /openapi/ instead of the SPA fallback', () => {
    // 不能回退 SPA index.html：未匹配的 API 前缀必须以 404 明确失败。
    expect(bcsOnlyConfig).toContain('location /api/ { return 404; }');
    expect(bcsOnlyConfig).toContain('location /openapi/ { return 404; }');
  });

  test('keeps healthz and SPA deep-link handling', () => {
    expect(bcsOnlyConfig).toContain('location = /healthz');
    // 静态服务继续承载发现页深链。
    expect(bcsOnlyConfig).toContain('try_files $uri $uri/ /index.html;');
  });
});

describe('BCS-only dev/prod route parity', () => {
  const originalEnv = { ...process.env };

  afterEach(() => {
    process.env = { ...originalEnv };
    jest.resetModules();
  });

  test('the dev proxy and the prod template send the same collaboration subtree to BCS', () => {
    const bcsUpstream = 'http://127.0.0.1:22100';
    process.env.FRONTEND_API_PROFILE = 'bcs-only';
    process.env.BCS_UPSTREAM = bcsUpstream;
    jest.resetModules();

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const config = require('../../config/config.local').default;
    const proxy = config.proxy as Record<string, { target: string; ws?: boolean }>;
    const bcsOnlyConfig = readFileSync(
      join(process.cwd(), 'deploy/nginx/default.bcs-only.conf.template'),
      'utf8',
    );

    // dev：整棵子树（含 tasks、bbs、WS）落到同一个 BCS 上游。
    expect(proxy['/openapi/v1/collaboration'].target).toBe(bcsUpstream);
    expect(proxy['/openapi/v1/collaboration'].ws).toBe(true);
    // prod：同一个前缀 location 覆盖同样的子树。
    expect(bcsOnlyConfig).toContain('location /openapi/v1/collaboration/ { proxy_pass ${BCS_UPSTREAM};');
    // 两侧的子树都没有被更窄的例外夺回：dev 里整棵键位于既有 tasks/BBS 键之前
    // （umi 按插入顺序匹配），prod 模板里也没有更窄的 collaboration 子 location。
    const keys = Object.keys(proxy);
    expect(keys.indexOf('/openapi/v1/collaboration')).toBeLessThan(
      keys.indexOf('/openapi/v1/collaboration/tasks'),
    );
    expect(keys.indexOf('/openapi/v1/collaboration')).toBeLessThan(
      keys.indexOf('/openapi/v1/collaboration/tasks/bbs'),
    );
    expect(proxy['/openapi/v1/collaboration/friend-connections']).toBeUndefined();
  });
});
