import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runInNewContext } from 'node:vm';

/**
 * 部署入口脚本契约（bcs-standalone-friend-discovery Task 5）：
 * FRONTEND_API_PROFILE=full|bcs-only 选择 API 前置形态，并把
 * window.AVERNET_RUNTIME_CONFIG.PUBLIC_BOT_BACKEND_ENABLED 写成 JS 布尔量。
 * 脚本内镜像路径按现有模式替换到临时目录后再执行。
 */

/** full profile 的全部必填上游变量（docs/deployment.md「Required upstreams」）。 */
const FULL_PROFILE_REQUIRED_VARS = [
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
] as const;

const FULL_PROFILE_REQUIRED_ENV: Record<string, string> = Object.fromEntries(
  FULL_PROFILE_REQUIRED_VARS.map((name) => [name, 'https://example.com/bcn']),
);

const fullTemplatePath = join(process.cwd(), 'deploy/nginx/default.conf.template');
const bcsOnlyTemplatePath = join(process.cwd(), 'deploy/nginx/default.bcs-only.conf.template');
const entrypointPath = join(process.cwd(), 'deploy/docker-entrypoint.sh');

interface EntrypointSandbox {
  scriptPath: string;
  runtimeConfigPath: string;
  /** /etc/nginx/templates/default.conf.template 的临时替换位。 */
  activeTemplatePath: string;
  cleanup: () => void;
}

/**
 * 把 entrypoint 里的镜像内路径（runtime-config.js 输出、bcs-only profile 模板源、
 * envsubst 模板目标）替换到临时目录；模板位面与 Dockerfile COPY 一致：
 * full 模板占位 templates/，bcs-only profile 模板位于不同的只读配置目录。
 */
function createEntrypointSandbox(): EntrypointSandbox {
  const directory = mkdtempSync(join(tmpdir(), 'avernet-runtime-'));
  const templateProfilesDir = join(directory, 'template-profiles');
  const templatesDir = join(directory, 'templates');
  mkdirSync(templateProfilesDir);
  mkdirSync(templatesDir);
  writeFileSync(join(templatesDir, 'default.conf.template'), readFileSync(fullTemplatePath, 'utf8'));
  writeFileSync(
    join(templateProfilesDir, 'default.bcs-only.conf.template'),
    readFileSync(bcsOnlyTemplatePath, 'utf8'),
  );
  const script = readFileSync(entrypointPath, 'utf8')
    .replace('/usr/share/nginx/html/runtime-config.js', join(directory, 'runtime-config.js'))
    .replace(
      '/etc/nginx/template-profiles/default.bcs-only.conf.template',
      join(templateProfilesDir, 'default.bcs-only.conf.template'),
    )
    .replace('/etc/nginx/templates/default.conf.template', join(templatesDir, 'default.conf.template'));
  const scriptPath = join(directory, 'entrypoint.sh');
  writeFileSync(scriptPath, script);
  return {
    scriptPath,
    runtimeConfigPath: join(directory, 'runtime-config.js'),
    activeTemplatePath: join(templatesDir, 'default.conf.template'),
    cleanup: () => rmSync(directory, { recursive: true, force: true }),
  };
}

interface EntrypointResult {
  failed: boolean;
  stderr: string;
}

/** 透传干净环境：剥离部署变量，避免宿主环境污染 profile 断言。 */
function runEntrypoint(scriptPath: string, env: Record<string, string>): EntrypointResult {
  const inherited = { ...process.env };
  for (const name of [
    ...FULL_PROFILE_REQUIRED_VARS,
    'BCS_UPSTREAM',
    'FRONTEND_API_PROFILE',
    'PUBLIC_BOT_BACKEND_ENABLED',
  ]) {
    delete inherited[name];
  }
  try {
    execFileSync('sh', [scriptPath], { env: { ...inherited, ...env }, encoding: 'utf8' });
    return { failed: false, stderr: '' };
  } catch (error) {
    return { failed: true, stderr: String((error as { stderr?: string }).stderr ?? '') };
  }
}

function readRuntimeConfig(path: string): Record<string, unknown> {
  const context = {
    window: {} as { AVERNET_RUNTIME_CONFIG?: Record<string, unknown> },
  };
  runInNewContext(readFileSync(path, 'utf8'), context);
  return context.window.AVERNET_RUNTIME_CONFIG ?? {};
}

it.each(['local', ''])('writes provider opt-in %s into deployed runtime configuration (default full profile)', (slug) => {
  const sandbox = createEntrypointSandbox();
  try {
    const result = runEntrypoint(sandbox.scriptPath, {
      ...FULL_PROFILE_REQUIRED_ENV,
      QWENWORK_PROVIDER_SLUG: slug,
      DSH_REGISTRATION_GUIDE_URL: slug ? 'https://docs.example.com/releases/0.2.0/dsh' : '',
    });
    expect(result).toEqual({ failed: false, stderr: '' });
    const runtimeConfig = readRuntimeConfig(sandbox.runtimeConfigPath);
    expect(runtimeConfig.QWENWORK_PROVIDER_SLUG).toBe(slug);
    expect(runtimeConfig.BCS_ENDPOINT_PRE).toBe('https://example.com/bcn');
    expect(runtimeConfig.DSH_REGISTRATION_GUIDE_URL).toBe(
      slug ? 'https://docs.example.com/releases/0.2.0/dsh' : '',
    );
    // full（缺省）profile：可选 Backend 增强保持开启，runtime 值为 JS 布尔量 true。
    expect(runtimeConfig.PUBLIC_BOT_BACKEND_ENABLED).toBe(true);
    expect(typeof runtimeConfig.PUBLIC_BOT_BACKEND_ENABLED).toBe('boolean');
    // full 模板保持原位，不被 bcs-only profile 模板替换。
    expect(readFileSync(sandbox.activeTemplatePath, 'utf8')).toBe(readFileSync(fullTemplatePath, 'utf8'));
  } finally {
    sandbox.cleanup();
  }
});

it('keeps requiring every full-stack upstream variable when FRONTEND_API_PROFILE=full', () => {
  const sandbox = createEntrypointSandbox();
  try {
    const env: Record<string, string> = { ...FULL_PROFILE_REQUIRED_ENV, FRONTEND_API_PROFILE: 'full' };
    delete env.TEAMCLAW_GATEWAY_UPSTREAM;
    const result = runEntrypoint(sandbox.scriptPath, env);
    expect(result.failed).toBe(true);
    expect(result.stderr).toContain('Missing required deployment variable: TEAMCLAW_GATEWAY_UPSTREAM');

    const denied = runEntrypoint(sandbox.scriptPath, { FRONTEND_API_PROFILE: 'full' });
    expect(denied.failed).toBe(true);
  } finally {
    sandbox.cleanup();
  }
});

it('bcs-only profile only requires BCS_UPSTREAM and swaps in the BCS-only template', () => {
  const sandbox = createEntrypointSandbox();
  try {
    const result = runEntrypoint(sandbox.scriptPath, {
      FRONTEND_API_PROFILE: 'bcs-only',
      BCS_UPSTREAM: 'http://127.0.0.1:21000',
      // Backend/Gateway/Admin/Task 等变量一概缺失，不得阻塞启动。
    });
    expect(result).toEqual({ failed: false, stderr: '' });
    // 缺省 envsubst 模板位已换成 bcs-only profile 模板（cp 发生在官方模板替换之前），
    // 且 BCS_UPSTREAM 归一值已写进 proxy_pass 本体。
    const rendered = readFileSync(sandbox.activeTemplatePath, 'utf8');
    expect(rendered).toContain('# BCS-only 同源代理 profile');
    expect(rendered).not.toContain('BCS_AUTH_UPSTREAM');
    expect(rendered).toContain('proxy_pass http://127.0.0.1:21000;');
    const runtimeConfig = readRuntimeConfig(sandbox.runtimeConfigPath);
    // 生成运行时 PUBLIC_BOT_BACKEND_ENABLED:false —— JS 布尔量，绝不能写字符串 'false'。
    expect(runtimeConfig.PUBLIC_BOT_BACKEND_ENABLED).toBe(false);
    expect(typeof runtimeConfig.PUBLIC_BOT_BACKEND_ENABLED).toBe('boolean');
    // BCS endpoint 运行时键保留：bcs-only 下为可选输入，缺省空串由前端回退编译期缺省。
    expect(runtimeConfig.BCS_ENDPOINT_PRE).toBe('');
    expect(runtimeConfig.BCS_ENDPOINT_PROD).toBe('');
  } finally {
    sandbox.cleanup();
  }
});

it('bcs-only profile without BCS_UPSTREAM refuses to start', () => {
  const sandbox = createEntrypointSandbox();
  try {
    const result = runEntrypoint(sandbox.scriptPath, { FRONTEND_API_PROFILE: 'bcs-only' });
    expect(result.failed).toBe(true);
    expect(result.stderr).toContain('Missing required deployment variable: BCS_UPSTREAM');
  } finally {
    sandbox.cleanup();
  }
});

it.each(['http://127.0.0.1:21000/', 'http://127.0.0.1:21000//'])(
  'normalizes trailing slashes on BCS_UPSTREAM into the rendered nginx template: %s',
  (upstream) => {
    const sandbox = createEntrypointSandbox();
    try {
      const host = upstream.replace(/\/+$/, '');
      const result = runEntrypoint(sandbox.scriptPath, {
        FRONTEND_API_PROFILE: 'bcs-only',
        BCS_UPSTREAM: upstream,
      });
      expect(result).toEqual({ failed: false, stderr: '' });

      // 归一化必须落在最终 nginx 配置：前缀 location 的 proxy_pass 带尾斜杠会替换
      // 掉匹配路径，反向代理语义被静默改写。
      const rendered = readFileSync(sandbox.activeTemplatePath, 'utf8');
      expect(rendered).toContain(`proxy_pass ${host};`);
      expect(rendered).not.toContain(`proxy_pass ${host}/;`);
      // 所有 proxy_pass 都拿到归一值，没有残留占位符（官方 envsubst 不会看到归一值）。
      expect(rendered).not.toContain('${BCS_UPSTREAM};');
    } finally {
      sandbox.cleanup();
    }
  },
);

it.each(['http://bcs:21000/api', 'http://bcs:21000/api/', 'https://bcs:21000/openapi'])(
  'refuses a BCS_UPSTREAM carrying a path part: %s',
  (upstream) => {
    const sandbox = createEntrypointSandbox();
    try {
      // 带 URI 的 proxy_pass 会改写转发路径；正则 location 下 nginx 甚至拒绝启动。
      const result = runEntrypoint(sandbox.scriptPath, {
        FRONTEND_API_PROFILE: 'bcs-only',
        BCS_UPSTREAM: upstream,
      });
      expect(result.failed).toBe(true);
      expect(result.stderr).toContain('BCS_UPSTREAM must be an origin without a path');
      expect(result.stderr).toContain(upstream);
    } finally {
      sandbox.cleanup();
    }
  },
);

it.each(['bcs:21000', 'ftp://bcs:21000', '//bcs:21000'])(
  'refuses a non-http(s) BCS_UPSTREAM: %s',
  (upstream) => {
    const sandbox = createEntrypointSandbox();
    try {
      const result = runEntrypoint(sandbox.scriptPath, {
        FRONTEND_API_PROFILE: 'bcs-only',
        BCS_UPSTREAM: upstream,
      });
      expect(result.failed).toBe(true);
      expect(result.stderr).toContain('BCS_UPSTREAM must start with http:// or https://');
    } finally {
      sandbox.cleanup();
    }
  },
);

it.each(['true', 'false'] as const)(
  'lets the full profile override PUBLIC_BOT_BACKEND_ENABLED=%s',
  (value) => {
    const sandbox = createEntrypointSandbox();
    try {
      const result = runEntrypoint(sandbox.scriptPath, {
        ...FULL_PROFILE_REQUIRED_ENV,
        PUBLIC_BOT_BACKEND_ENABLED: value,
      });
      expect(result).toEqual({ failed: false, stderr: '' });
      expect(readRuntimeConfig(sandbox.runtimeConfigPath).PUBLIC_BOT_BACKEND_ENABLED).toBe(value === 'true');
    } finally {
      sandbox.cleanup();
    }
  },
);

it('refuses an invalid PUBLIC_BOT_BACKEND_ENABLED in the full profile', () => {
  const sandbox = createEntrypointSandbox();
  try {
    const result = runEntrypoint(sandbox.scriptPath, {
      ...FULL_PROFILE_REQUIRED_ENV,
      PUBLIC_BOT_BACKEND_ENABLED: 'yes',
    });
    expect(result.failed).toBe(true);
    expect(result.stderr).toContain('PUBLIC_BOT_BACKEND_ENABLED must be true or false');
  } finally {
    sandbox.cleanup();
  }
});

it('keeps bcs-only PUBLIC_BOT_BACKEND_ENABLED fixed to false', () => {
  const sandbox = createEntrypointSandbox();
  try {
    const result = runEntrypoint(sandbox.scriptPath, {
      FRONTEND_API_PROFILE: 'bcs-only',
      BCS_UPSTREAM: 'http://127.0.0.1:21000',
      PUBLIC_BOT_BACKEND_ENABLED: 'true',
    });
    expect(result).toEqual({ failed: false, stderr: '' });
    // bcs-only 没有 Backend：外部覆盖不生效，仍必须写 JS 布尔量 false。
    expect(readRuntimeConfig(sandbox.runtimeConfigPath).PUBLIC_BOT_BACKEND_ENABLED).toBe(false);
  } finally {
    sandbox.cleanup();
  }
});

it('refuses to start on an unknown FRONTEND_API_PROFILE before validating any variable', () => {
  const sandbox = createEntrypointSandbox();
  try {
    const result = runEntrypoint(sandbox.scriptPath, {
      ...FULL_PROFILE_REQUIRED_ENV,
      FRONTEND_API_PROFILE: 'staging',
    });
    expect(result.failed).toBe(true);
    expect(result.stderr).toContain('Unknown FRONTEND_API_PROFILE: staging');
    expect(result.stderr).not.toContain('Missing required deployment variable');
  } finally {
    sandbox.cleanup();
  }
});
