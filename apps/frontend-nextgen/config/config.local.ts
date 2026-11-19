import { defineConfig } from '@umijs/max';
import { PRESETS, type PresetName } from './presets.config';

const presetName = (process.env.PRESET || 'local') as PresetName;
const preset = PRESETS[presetName];
if (!preset) throw new Error(`Unknown PRESET: ${presetName}`);

const gateway = process.env.TEAMCLAW_GW_BASE || preset.servers.TEAMCLAW_GW;
const bcsEndpointPre = process.env.BCS_ENDPOINT_PRE || preset.servers.BCS_ENDPOINT_PRE;
const bcsEndpointProd = process.env.BCS_ENDPOINT_PROD || preset.servers.BCS_ENDPOINT_PROD;
// Internal task APIs may use a dedicated Engine; public /openapi task APIs always use Gateway.
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

// 开发态 API 前置形态与部署 FRONTEND_API_PROFILE 同名同值（docs/deployment.md）：
// full（缺省）不改任何代理与开关；bcs-only 编译关闭可选 Backend 增强，
// 并把好友发现已定义的 BCS 子树优先代理到 BCS，不引入私有域名。
const apiProfile = process.env.FRONTEND_API_PROFILE || 'full';
if (apiProfile !== 'full' && apiProfile !== 'bcs-only') {
  throw new Error(`Unknown FRONTEND_API_PROFILE: ${apiProfile}`);
}
const bcsOnlyProfile = apiProfile === 'bcs-only';
// 仅本地排障用：BCS-only 上游；缺省回退当前 preset 的 BCS endpoint 占位。
const bcsUpstream = process.env.BCS_UPSTREAM || bcsEndpointPre;

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
    // Local task-panel source testing; the asset process is started independently.
    '/task-panel': proxy('http://127.0.0.1:43820'),
    '/auth': proxy(auth),
    '/openapi/v1/auth': proxy(auth),
    // BCS-only 开发 profile：好友发现整棵 collaboration 子树优先于其余通用键
    // （umi proxy 按 key 插入顺序匹配），与部署 bcs-only 模板的整棵转发一致；
    // 内部候选搜索必须先于 /api 通配键。群聊 WS 需要 ws:true。
    ...(bcsOnlyProfile
      ? {
          '/openapi/v1/collaboration': proxy(bcsUpstream, true),
          '/api/v1/collaboration/bots/*/candidates/search': proxy(bcsUpstream),
        }
      : {}),
    '/openapi/v1/bots/work-order-notifications': proxy(admin),
    '/openapi/v1/bots/work-orders': proxy(admin),
    '/openapi/v1/bots/spaces': proxy(admin),
    // 任务公开接口（包括 BBS）统一经过 Gateway，不直连 Task Engine。
    // 预发时 gateway 由 TEAMCLAW_GW_BASE 注入为 teamclawgw-pre，公开仓库不硬编码部署域名。
    '/openapi/v1/collaboration/tasks/bbs': proxy(gateway, true),
    '/openapi/v1/collaboration/tasks/bots/*/bbs/**': proxy(gateway, true),
    // RSI uses the same authenticated Gateway as every other Bot capability.
