import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runInNewContext } from 'node:vm';

it.each(['local', ''])('writes provider opt-in %s into deployed runtime configuration', (slug) => {
  const directory = mkdtempSync(join(tmpdir(), 'qwen-runtime-'));
  try {
    const script = readFileSync(join(process.cwd(), 'deploy/docker-entrypoint.sh'), 'utf8').replace(
      '/usr/share/nginx/html/runtime-config.js',
      join(directory, 'runtime-config.js'),
    );
    const path = join(directory, 'entrypoint.sh');
    writeFileSync(path, script);
    const keys = [
      'BCS_AUTH_UPSTREAM',
      'TASK_ENGINE_UPSTREAM',
      'TEAMCLAW_GATEWAY_UPSTREAM',
      'TEAMCLAW_ADMIN_UPSTREAM',
      'PRIVATE_CHAT_MANAGEMENT_UPSTREAM',
      'PRIVATE_CHAT_SESSION_UPSTREAM',
      'CLAWWEB_UPSTREAM',
      'AIXCORE_UPSTREAM',
      'BCS_ENDPOINT_PRE',
      'BCS_ENDPOINT_PROD',
    ];
    execFileSync('sh', [path], {
      env: {
        ...process.env,
        ...Object.fromEntries(keys.map((key) => [key, 'https://example.com/bcn'])),
        QWENWORK_PROVIDER_SLUG: slug,
      },
    });
    const context = {
      window: {} as { AVERNET_RUNTIME_CONFIG?: { QWENWORK_PROVIDER_SLUG: string; BCS_ENDPOINT_PRE: string } },
    };
    runInNewContext(readFileSync(join(directory, 'runtime-config.js'), 'utf8'), context);
    expect(context.window.AVERNET_RUNTIME_CONFIG?.QWENWORK_PROVIDER_SLUG).toBe(slug);
    expect(context.window.AVERNET_RUNTIME_CONFIG?.BCS_ENDPOINT_PRE).toBe('https://example.com/bcn');
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
