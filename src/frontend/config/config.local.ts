/**
 * 开发环境配置 - 预设模式
 *
 * 使用方式：
 * 1. npm run dev          - 使用预发环境（默认）
 * 2. npm run dev:local    - 使用本地后端
 * 3. npm run dev:dev      - 使用内网开发环境
 * 4. npm run dev:pre      - 使用预发环境
 * 5. npm run dev:prod     - 使用生产环境
 *
 * 配置文件：
 * - config/servers.config.ts  - 服务器地址配置（唯一配置源）
 * - config/presets.config.ts  - 环境预设配置
 *
 * 旧配置备份：config/config.local.backup.ts
 */

import { defineConfig } from '@umijs/max';
import { PRESETS, type PresetName } from './presets.config';

// ==================== 读取环境变量 ====================

// 从环境变量读取预设名称，默认为 'pre'
const presetName = (process.env.PRESET || 'pre') as PresetName;
const preset = PRESETS[presetName];

// 验证预设是否存在
if (!preset) {
  const availablePresets = Object.keys(PRESETS).join(', ');
  throw new Error(
    `\n❌ 未知的预设: "${presetName}"\n` +
      `✅ 可用预设: ${availablePresets}\n` +
      `💡 使用方式: PRESET=pre npm run dev\n`,
  );
}

// ==================== 自动检测是否需要 HTTPS ====================

/**
 * 检测服务器配置中是否有使用 HTTPS
 * 排除不影响前端协议的服务器（如 ASFAGENT）
 * 如果其他服务器使用了 HTTPS，则前端也需要启用 HTTPS
 * const EXCLUDED_SERVERS: (keyof typeof preset.servers)[] = [
    'ASFAGENT',
    'AIXCORE',
    'MCPCENTER',
  ];
  const hasHttpsServer = Object.entries(preset.servers).some(
    ([key, url]) =>
      !EXCLUDED_SERVERS.includes(key as keyof typeof preset.servers) &&
      url.startsWith('https://'),
  );
 */

// HARNESSFLOW 走 dev proxy 代理（浏览器不直连），不影响前端协议选择
const PROTOCOL_EXCLUDED_SERVERS = ['HARNESSFLOW'];

const hasHttpServer = Object.entries(preset.servers).some(
  ([key, url]) =>
    !PROTOCOL_EXCLUDED_SERVERS.includes(key) && url.startsWith('http://'),
);

// ==================== 启动信息 ====================

const frontendPort = process.env.PORT || '8000';
const frontendUrl = !hasHttpServer
  ? `https://dev.localhost:${frontendPort}`
  : `http://dev.localhost:${frontendPort}`;
const hostConfig = !hasHttpServer
  ? '127.0.0.1 dev.localhost'
  : '127.0.0.1 dev.localhost';
const localDevEnv =
  presetName === 'local'
    ? 'LOCAL'
    : presetName === 'dev'
    ? 'DEV'
    : presetName === 'prod'
    ? 'PROD'
    : 'PRE';

console.log(`
╔════════════════════════════════════════════════════╗
║  OC-Web 开发环境                                   ║
╠════════════════════════════════════════════════════╣
║  环境名称: ${preset.name.padEnd(38)}║
║  管理服务: ${preset.servers.MANAGEMENT.padEnd(38)}║
║  会话服务: ${preset.servers.SESSION.padEnd(38)}║
║  前端协议: ${(!hasHttpServer ? 'HTTPS (自动检测)' : 'HTTP').padEnd(38)}║
║  前端地址: ${frontendUrl.padEnd(38)}║
║  日志级别: ${preset.logLevel.padEnd(38)}║
╠════════════════════════════════════════════════════╣
║  ⚠️  请确保已配置 hosts:                           ║
║  ${hostConfig.padEnd(46)}║
╚════════════════════════════════════════════════════╝
`);

// ==================== Proxy 配置生成器 ====================

/**
 * 创建 proxy 配置
 */
const createProxyConfig = (
  target: string,
  options: Record<string, any> = {},
) => ({
  target,
  changeOrigin: true, // 修改 origin header，解决跨域
  secure: false, // 允许自签名 HTTPS 证书
  logLevel: preset.logLevel, // 日志级别
  ...options,
});

// ==================== 本地 harness-data 调试开关 ====================
// 设置 LOCAL_HARNESS=1 时，前端 data-proxy 通道（/data/*）不再走 proxypass
// 打到预发引擎，而是由 dev server proxy 直连本机 harness-data
// （默认 http://localhost:8765，可用 LOCAL_HARNESS_URL 覆盖）。
//
// 配套：requestConfig.ts 读 define 注入的 LOCAL_HARNESS_DATA 常量，命中时
// 跳过 proxypass 改写、原样透出 /data/*，由下方 proxy 规则接管。
// /data 前缀保留——harness-data 的 serve 侧用 strip_data_prefix 统一剥掉。
//
// 仅影响 /data/* 这一前缀，其它 /api/* 仍走对应预设的 MANAGEMENT / proxypass。

const LOCAL_HARNESS_ENABLED = process.env.LOCAL_HARNESS === '1';
const LOCAL_HARNESS_TARGET =
  process.env.LOCAL_HARNESS_URL || 'http://localhost:8765';

if (LOCAL_HARNESS_ENABLED) {
  console.log(
    `🔧 LOCAL_HARNESS=1 → /data/* 将直连本机 harness-data ${LOCAL_HARNESS_TARGET}（绕过 proxypass）`,
  );
}

// ==================== aixharness Langfuse 接入（devFlow 工作流看板）====================
// devFlow（aix-engine 工作流）看板的数据走 aixharness Web Server 的 /langfuse/* 接口。
// 前端统一发 /aixharness/langfuse/*（见 src/services/backend-api/WorkflowController.ts），
// 这里把 /aixharness 前缀剥掉后转发到目标 aixharness（路由前缀就是 /langfuse、/dima 等）。
//
// 目标按预设走（与 aixcoding 一致）：local→localhost:8888、dev/pre→预发 aixharness、prod→生产 aixharness；
// 用 AIXHARNESS_BASE 可显式覆盖。即 `PRESET=pre`（含 devs:pre:harness）会让 devFlow 看板
// 从预发 aixharness（连预发 Langfuse）取数据，无需额外设环境变量。
// 预发 aixharness 有 SSO 网关，登录态走浏览器 cookie（前端跑在本地 dev host）。
// 生产侧路由（config.ts 的 tern.proxy）后续再补，故 devFlow 卡片当前仅在 dev 展示。
const AIXHARNESS_TARGET =
  process.env.AIXHARNESS_BASE || preset.servers.AIXHARNESS;

console.log(
  `🔧 /aixharness/* → ${AIXHARNESS_TARGET}（aixharness Langfuse，devFlow 工作流看板）`,
);

// ==================== BCS 后端端口（环境变量单一事实源）====================
// BCS server / BCSFUSE 的端口由本地后端启动脚本经 .env.local 的 BCS_PORT / BCSFUSE_PORT
// 决定（默认 21000 / 8765）。dev proxy 从同一环境变量派生目标，避免后端改端口后
// servers.config.ts 的占位默认漂移成连旧端口（OSS 业务请求全走 /bcnproxy → BCS）。
// 仅本地后端（127.0.0.1）场景适用；未设时回退预设占位值，dev/pre/prod 指向内网
// 真实地址，不受影响。
const BCS_TARGET = process.env.BCS_PORT
  ? `http://127.0.0.1:${process.env.BCS_PORT}`
  : preset.servers.BCN || 'http://127.0.0.1:21000';
const BCSFUSE_TARGET = process.env.BCSFUSE_PORT
  ? `http://127.0.0.1:${process.env.BCSFUSE_PORT}`
  : preset.servers.BCNFUSE || 'http://127.0.0.1:8765';

if (process.env.BCS_PORT || process.env.BCSFUSE_PORT) {
  console.log(
    `🔧 /bcnproxy/* → ${BCS_TARGET}（BCS server）｜/bcnfuse/* → ${BCSFUSE_TARGET}（BCSFUSE）`,
  );
}

// ==================== 副屏 UMD 组件 CDN 同源代理（dev-only）====================
// 副屏 UMD 组件由 UmdLoader 在浏览器里 fetch(cdn) 拉取（cdn 是后端下发的绝对地址）。
// fetch 受 CORS 约束：受限 CDN 只放行白名单内的 origin，导致
// 用 localhost 打开时副屏「组件加载失败」。
//
// 解法：dev 下让前端把 fetch 改写成同源路径 /__umd_cdn?target=<绝对CDN地址>，
// 由 dev server 转发到真实 CDN。浏览器只与 dev server 同源通信（不触发 CORS），
// dev server 用 Node 拉 CDN 是服务端请求（不受 CORS 白名单限制）。于是无论 CDN
// 换成哪个域名、前端用 localhost 还是 dev.localhost，都能加载——前端域名与 CDN
// 域名彻底解耦。配套改写见 src/components/UmdLoader/loadUmd.ts（同名 UMD_CDN_PROXY
// define 守卫；生产不注入，直连原始 CDN）。
//
// ⚠️ 路径常量 '/__umd_cdn' 与 loadUmd.ts 中的 UMD_CDN_PROXY_PATH 必须保持一致。
const UMD_CDN_PROXY_PATH = '/__umd_cdn';
const parseUmdProxyTarget = (reqUrl: string): URL | null => {
  try {
    const target = new URL(reqUrl, 'http://localhost').searchParams.get(
      'target',
    );
    return target ? new URL(target) : null;
  } catch {
    return null;
  }
};

// ==================== BCN 一期闭包接口守卫开关 ====================
// BCN 一期开源：业务请求应全部走 /bcnproxy（BCN 后端），不应触达 /api
// （内部 CloudBrain / 管理后端）。设 BCN_API_GUARD=1 时，requestConfig.ts 在 dev 下
// 对违规的 /api 请求告警（仅告警、不阻断，含调用栈），用于逐个揪出运行时漏发的
// /api 接口（如 useBot 的 fetchTotalBotCount 在挂载时自动触发的 by-owner-or-collaborator）。
//
// 内部仓默认关：避免对 Assistant 等正常依赖 /api 的页面误报，需显式
// `BCN_API_GUARD=1 npm run dev:local` 开启，并只在 BCN 页面（/bcn/*）下验证。
// 开源仓：在其 dev/CI config 里默认置 true（整个 app 即 BCN）。
const BCN_API_GUARD_ENABLED = process.env.BCN_API_GUARD === '1';

if (BCN_API_GUARD_ENABLED) {
  console.log(
    '🔧 BCN_API_GUARD=1 → dev 下将对 BCN 闭包内违规的 /api 调用告警（仅告警，不阻断）',
  );
}

// ==================== 乾坤微前端配置（内部 overlay）====================
// 本地调试 aix-card 子应用的真实微前端资源地址属内部，下沉 config/qiankun.internal.ts。
// 内部构建（UMI_ENV=internal）条件 require 真实配置；开源 dev 无 qiankun（不调 aix-card，可接受）。
const internalQiankun =
  process.env.UMI_ENV === 'internal'
    ? // eslint-disable-next-line @typescript-eslint/no-var-requires
      require('./qiankun.internal').default
    : undefined;

// ==================== Bigfish 配置 ====================

export default defineConfig({
  // ⚠️ umi/max 迁移补偿（2026-06-12）：
  // bigfish appType:'console'/deployMode:'tern' 在 dev 时由 Tern 注入 window.__TERN__，
  // 其中 _localDev:true 驱动 isLocalDev()（src/utils/platform.ts）走 DevUserInput 本地登录流程。
  // 迁移剥离 Tern 后该对象消失 → authenticate() 拿不到身份 → 卡「SSO 身份验证」弹框。
  // 此处在 dev HTML 头部手动注入，恢复本地开发免 SSO 体验。
  // 正式迁移：内部鉴权（Tern/__TERN__/SSO）整体属内部能力，由 config.internal.ts + auth adapter 承接。
  headScripts: [
    { content: 'window.__TERN__ = window.__TERN__ || { _localDev: true };' },
  ],

  // Proxy 配置
  proxy: {
    // 本地 harness-data 调试（优先匹配，需在 /api 之前声明）：
    // /data/* 直连本机 harness-data，保留 /data 前缀（serve 侧自行 strip）。
    ...(LOCAL_HARNESS_ENABLED
      ? {
          '/data': createProxyConfig(LOCAL_HARNESS_TARGET),
        }
      : {}),

    // 管理 API（云端模式下 /data/* 已由 requestConfig 改写为 /proxypass/<target>/...
    // 实际走下方 /proxypass 规则，这里的 /api 兜底其余管理接口）
    '/api': createProxyConfig(preset.servers.MANAGEMENT),

    // HarnessFlow 工作流引擎 API
    '/harnessflow': createProxyConfig(
      preset.servers.HARNESSFLOW || 'http://localhost:3001',
      {
        pathRewrite: { '^/harnessflow': '' },
      },
    ),

    // 会话代理（支持 WebSocket）；评测 SSE 也走这条 /proxypass
    '/proxypass': createProxyConfig(preset.servers.SESSION, {
      ws: true, // 启用 WebSocket 代理
    }),

    // aixharness Langfuse 接口（devFlow 工作流看板）：剥掉 /aixharness 前缀转发
    // 到本机 aixharness Web Server（路由前缀就是 /langfuse、/dima 等）。
    '/aixharness': createProxyConfig(AIXHARNESS_TARGET, {
      pathRewrite: { '^/aixharness': '' },
    }),

    // 访问控制 API
    '/access': createProxyConfig(preset.servers.MANAGEMENT, {
      pathRewrite: { '^/access': '' }, // 移除 /access 前缀
    }),

    // ASF Agent API
    '/asfagent': createProxyConfig(preset.servers.ASFAGENT, {
      pathRewrite: { '^/asfagent': '' }, // 移除 /asfagent 前缀
    }),

    // ASF aixcore API - system bot endpoints
    '/aixcore/v1': createProxyConfig(
      preset.servers.AIXCORE || 'http://localhost:8888',
      {
        pathRewrite: { '^/aixcore/v1': '/api/v1' },
      },
    ),

    // ASF aixcore API - fallback for other aixcore paths
    '/aixcore': createProxyConfig(
      preset.servers.AIXCORE || 'http://localhost:8888',
      {
        pathRewrite: { '^/aixcore/(.*)': '/api/$1' },
      },
    ),

    // BCN 群聊服务 API
    '/bcnproxy': createProxyConfig(BCS_TARGET, {
      pathRewrite: { '^/bcnproxy': '' }, // 移除 /bcn 前缀
    }),

    // BCS Fuse 智能融合 API
    '/bcnfuse': createProxyConfig(BCSFUSE_TARGET, {
      pathRewrite: { '^/bcnfuse': '' }, // 移除 /bcnfuse 前缀
    }),

    // MCP Center 办公网权限申请 API
    '/mcpcenter': createProxyConfig(preset.servers.MCPCENTER, {
      pathRewrite: { '^/mcpcenter': '' }, // 移除 /mcpcenter 前缀
    }),

    // SecBaas API
    '/secbaas': createProxyConfig(
      preset.servers.SECBAAS || 'http://localhost:8888',
      {
        pathRewrite: { '^/secbaas': '' },
      },
    ),

    // 架构工作台 ADC API
    '/adc': createProxyConfig(preset.servers.ADC || 'http://localhost:8888', {
      pathRewrite: { '^/adc': '' }, // 移除 /adc 前缀
    }),

    // 副屏 UMD 组件 CDN 同源代理（dev-only，绕过 CDN 的 CORS 白名单，详见上方说明）。
    // 目标 origin 与 path 都按 ?target= 里的绝对 CDN 地址逐请求动态决定。
    [UMD_CDN_PROXY_PATH]: createProxyConfig('http://127.0.0.1', {
      router: (req: any) => {
        const u = parseUmdProxyTarget(req.url);
        return u ? u.origin : 'http://127.0.0.1';
      },
      pathRewrite: (path: string, req: any) => {
        const u = parseUmdProxyTarget(req.url);
        return u ? u.pathname + u.search : path;
      },
    }),
  },

  // HTTPS 配置（自动检测是否需要启用）
  ...(!hasHttpServer
    ? {
        https: {
          http2: false, // 禁用 HTTP/2（避免 chunks 加载问题）
        },
      }
    : {}),

  // ==================== 乾坤微前端配置 ====================
  // 用于在本地调试 aix-card 子应用，启用后主应用可加载并渲染 aix-card 微应用。
  // 真实微前端资源地址属内部，经 config/qiankun.internal.ts 注入（见上方 internalQiankun）。
  // 生产环境由部署平台注入配置，此处仅用于本地开发调试。
  ...(internalQiankun ? { qiankun: internalQiankun } : {}),

  // 注入环境变量到运行时
  define: {
    // 用于 src/utils/env.ts 中判断环境
    LOCAL_DEV_ENV: localDevEnv,
    // 用于 requestConfig.ts 判断是否让 /data/* 走本地 harness-data 调试
    LOCAL_HARNESS_DATA: LOCAL_HARNESS_ENABLED,
    // 用于 requestConfig.ts 的 BCN 闭包守卫：dev 下对违规 /api 调用告警
    BCN_API_GUARD: BCN_API_GUARD_ENABLED,
    // 用于 UmdLoader 的副屏 CDN 同源代理：dev 下把 fetch(cdn) 改写到 /__umd_cdn
    // 走 dev server 转发，绕过 CDN 的 CORS 白名单（生产不注入，直连原始 CDN）。
    UMD_CDN_PROXY: true,
    // 群聊 WS 的 BCS 基址（含 BCS_PORT）。dev 下注入，让 connectionStore 直连
    // ws://127.0.0.1:<BCS_PORT>/ws，跟随自定义端口；生产不注入，走 getServers().BCN。
    DEV_BCN_BASE: BCS_TARGET,
  },
});
