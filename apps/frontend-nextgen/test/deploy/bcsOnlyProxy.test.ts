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
    // 好友发现协作子树：bots/mine、bots/{actor}/candidates、friend-connections/**。
    expect(bcsOnlyConfig).toContain('location /openapi/v1/collaboration/ { proxy_pass ${BCS_UPSTREAM};');
    // BCS 内部语义候选搜索（智能搜索）精确正则；正则 location 优先于前缀 location。
    expect(bcsOnlyConfig).toContain(
      'location ~ ^/api/v1/collaboration/bots/[^/]+/candidates/search(?:/|$) { proxy_pass ${BCS_UPSTREAM};',
    );
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
