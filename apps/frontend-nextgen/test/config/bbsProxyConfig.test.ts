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
    expect(keys.indexOf('/openapi/v1/collaboration/tasks/bbs')).toBeLessThan(
      keys.indexOf('/openapi/v1/collaboration/tasks'),
    );
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
