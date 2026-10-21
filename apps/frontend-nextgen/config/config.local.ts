import { defineConfig } from '@umijs/max';
import { PRESETS, type PresetName } from './presets.config';

const presetName = (process.env.PRESET || 'local') as PresetName;
const preset = PRESETS[presetName];
if (!preset) throw new Error(`Unknown PRESET: ${presetName}`);

const gateway = process.env.TEAMCLAW_GW_BASE || preset.servers.TEAMCLAW_GW;
const bcsEndpointPre = process.env.BCS_ENDPOINT_PRE || preset.servers.BCS_ENDPOINT_PRE;
const bcsEndpointProd = process.env.BCS_ENDPOINT_PROD || preset.servers.BCS_ENDPOINT_PROD;
// Task APIs may be deployed as a dedicated service. Default to Gateway so a single-upstream
// Open Core deployment remains runnable; operators can split it with TASK_ENGINE_UPSTREAM.
const taskEngine = process.env.TASK_ENGINE_UPSTREAM || gateway;
const admin = process.env.TEAMCLAW_ADMIN_BASE || preset.servers.TEAMCLAW_ADMIN;
const privateChatManagement =
  process.env.TEAMCLAW_PRIVATE_CHAT_MANAGEMENT_BASE || preset.servers.PRIVATE_CHAT_MANAGEMENT;
const privateChatSession = process.env.TEAMCLAW_PRIVATE_CHAT_SESSION_BASE || preset.servers.PRIVATE_CHAT_SESSION;
const clawweb = process.env.TEAMCLAW_CLAWWEB_BASE || preset.servers.CLAWWEB;
const auth = process.env.TEAMCLAW_AUTH_BASE || bcsEndpointPre;
const loginStrategy = process.env.TEAMCLAW_LOGIN_STRATEGY || 'oauth-provider';
const taskApiBase = process.env.TEAMCLAW_TASK_API_BASE || '/openapi/v1/collaboration/tasks';
const devHttpsEnabled = process.env.TEAMCLAW_DEV_HTTPS === '1';
const devHttpsHost = process.env.TEAMCLAW_DEV_HTTPS_HOST || 'localhost';

if (loginStrategy !== 'oauth-provider' && loginStrategy !== 'ace-gateway') {
  throw new Error(`Unknown TEAMCLAW_LOGIN_STRATEGY: ${loginStrategy}`);
}
if (taskApiBase !== '/openapi/v1/collaboration/tasks' && taskApiBase !== '/api/v1/collaboration/tasks') {
  throw new Error(`Unknown TEAMCLAW_TASK_API_BASE: ${taskApiBase}`);
}

const proxy = (target: string, ws = false) => ({ target, changeOrigin: true, secure: false, ws });

export default defineConfig({
  mfsu: false,
  https: devHttpsEnabled ? { hosts: [devHttpsHost], http2: false } : undefined,
  proxy: {
    '/auth': proxy(auth),
    '/openapi/v1/auth': proxy(auth),
    '/openapi/v1/bots/work-order-notifications': proxy(admin),
    '/openapi/v1/bots/work-orders': proxy(admin),
    '/openapi/v1/bots/spaces': proxy(admin),
    // BBS 已登记到 Gateway；两个更窄的匹配必须位于通用 task engine 路由之前。
    // 预发时 gateway 由 TEAMCLAW_GW_BASE 注入为 teamclawgw-pre，公开仓库不硬编码部署域名。
    '/openapi/v1/collaboration/tasks/bbs': proxy(gateway, true),
    '/openapi/v1/collaboration/tasks/bots/*/bbs/**': proxy(gateway, true),
    // 其余任务执行/进度/授权仍可走独立 Task Engine。
    '/openapi/v1/collaboration/tasks': proxy(taskEngine, true),
    '/openapi': proxy(gateway, true),
    // 本地直连 Task Engine 调试：必须位于通用 collaboration Gateway 路由之前。
    '/api/v1/collaboration/tasks': proxy(taskEngine, true),
    '/api/v1/collaboration': proxy(gateway, true),
    '/api/workflows': proxy(clawweb),
    '/api': proxy(privateChatManagement),
    '/proxypass': proxy(privateChatSession, true),
    '/bcnproxy': { ...proxy(gateway), pathRewrite: { '^/bcnproxy': '/api/v1/collaboration' } },
    '/docs': proxy(gateway),
    '/aixcore': {
      ...proxy(preset.servers.AIXCORE || 'http://127.0.0.1:8888'),
      pathRewrite: { '^/aixcore/(.*)': '/api/$1' },
    },
  },
  define: {
    TEAMCLAW_LOGIN_STRATEGY: loginStrategy,
    TEAMCLAW_TASK_API_BASE: taskApiBase,
    TEAMCLAW_DEV_ENV:
      presetName === 'prod' ? 'PROD' : presetName === 'pre' ? 'PRE' : presetName === 'dev' ? 'DEV' : 'LOCAL',
    BCS_ENDPOINT_PRE: bcsEndpointPre,
    BCS_ENDPOINT_PROD: bcsEndpointProd,
    TEAMCLAW_OPENAPI_USER_ID: '',
    TEAMCLAW_GW_BASE: gateway,
    TEAMCLAW_ADMIN_BASE: admin,
    TEAMCLAW_PRIVATE_CHAT_MANAGEMENT_BASE: privateChatManagement,
    TEAMCLAW_PRIVATE_CHAT_SESSION_BASE: privateChatSession,
    TEAMCLAW_CLAWWEB_BASE: clawweb,
    TEAMCLAW_WS_GW_PRE: gateway,
    TEAMCLAW_WS_GW_PROD: gateway,
  },
});
