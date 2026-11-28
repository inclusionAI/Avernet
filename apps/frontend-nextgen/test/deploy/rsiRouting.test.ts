import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runInNewContext } from 'node:vm';

function deployment(
  env: Record<string, string>,
  check: (directory: string, script: string, variables: NodeJS.ProcessEnv) => void,
) {
  const directory = mkdtempSync(join(tmpdir(), 'rsi-routing-'));
  try {
    const source = readFileSync(join(process.cwd(), 'deploy/docker-entrypoint.sh'), 'utf8')
      .replace('/usr/share/nginx/html/runtime-config.js', join(directory, 'runtime-config.js'));
    const script = join(directory, 'entrypoint.sh');
    writeFileSync(script, source);
    const required = [
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
    const variables = {
      ...process.env,
      ...Object.fromEntries(required.map((key) => [key, 'http://gateway:8080'])),
      RSI_ENABLED: 'false',
      EVOLUTION_UPSTREAM: '',
      ...env,
    };
    check(directory, script, variables);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

it('keeps RSI off and existing gateway routing unchanged by default', () => {
  deployment({}, (directory, script, env) => {
    execFileSync('sh', [script], { env });
    const context = { window: { AVERNET_RUNTIME_CONFIG: { RSI_ENABLED: true } } };
    runInNewContext(readFileSync(join(directory, 'runtime-config.js'), 'utf8'), context);
    expect(context.window.AVERNET_RUNTIME_CONFIG.RSI_ENABLED).toBe(false);
  });
});

it('enables RSI through the existing authenticated Gateway, without a direct upstream', () => {
  deployment({ RSI_ENABLED: 'true' }, (directory, script, env) => {
    execFileSync('sh', [script], { env });
    const template = readFileSync('deploy/nginx/default.conf.template', 'utf8');
    expect(template).not.toContain('evolution-routes.inc');
    expect(template).not.toContain('EVOLUTION_UPSTREAM');
    expect(readFileSync(script, 'utf8')).not.toContain('proxy_pass');
    const context = { window: { AVERNET_RUNTIME_CONFIG: { RSI_ENABLED: false } } };
    runInNewContext(readFileSync(join(directory, 'runtime-config.js'), 'utf8'), context);
    expect(context.window.AVERNET_RUNTIME_CONFIG.RSI_ENABLED).toBe(true);
  });
});

it('rejects invalid RSI opt-in', () => {
  deployment({ RSI_ENABLED: '1' }, (_directory, script, env) => {
    expect(spawnSync('sh', [script], { env }).status).not.toBe(0);
  });
});
