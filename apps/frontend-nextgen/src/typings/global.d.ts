declare module '*.less';

/** 仅 PRESET=local 注入；非本地构建固定为空字符串。 */
declare const TEAMCLAW_OPENAPI_USER_ID: string;

/**
 * agentswarm 运行所属的 tenant（define 注入）。
 * 该网关把 tenant 作为显式请求参数而非取自登录主体，本版本固定为 `teamclaw`。
 */
declare const TEAMCLAW_AGENTSWARM_TENANT: string | undefined;

/** 登录策略（本地开发可覆盖）：内部 PRE 联调使用 ACE 网关。 */
declare const TEAMCLAW_LOGIN_STRATEGY: 'ace-gateway' | 'oauth-provider' | undefined;

/** Task 模块同源路径前缀；本地可切到 /api/v1 直连 Task Engine 排查。 */
declare const TEAMCLAW_TASK_API_BASE:
  | '/openapi/v1/collaboration/tasks'
  | '/api/v1/collaboration/tasks'
  | undefined;

/** 开发环境名称（define 注入）：LOCAL / DEV / PRE / PROD。生产构建为 PRE/PROD。 */
declare const TEAMCLAW_DEV_ENV: 'LOCAL' | 'DEV' | 'PRE' | 'PROD' | undefined;

/**
 * WebSocket 直连网关地址（define 注入），用于部署态绕过 tern cors proxy 的 WS 盲区。
 * - dev 构建由 config/config.local.ts 注入占位（dev 下 WS 走 dev-server proxy，不消费此值）
 * - 生产构建由 config/internal/runtime/config.ts bigfishProdConfig.define 注入真实域名
 * resolveGroupWsOrigin() 按当前 hostname 检测 PRE/PROD 取对应值，转为 wss:// 前缀。
 */
declare const TEAMCLAW_WS_GW_PRE: string;
declare const TEAMCLAW_WS_GW_PROD: string;
declare const TEAMCLAW_MONACO_CDN_PREFIX: string;

/** BCS endpoint（define 注入）：注册 CLI 按 PRE/PROD 选择其一。 */
declare const BCS_ENDPOINT_PRE: string;
declare const BCS_ENDPOINT_PROD: string;

/** Deployment opt-in after Provider provisioning; empty disables QwenWork onboarding. */
declare const QWENWORK_PROVIDER_SLUG: string;

/**
 * 可选 Backend 增强开关（BCS 独立好友发现，define 注入）：本地 BCS-only 排障经
 * TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED=false 编译关闭，构建默认全栈 true（config/config.ts）。
 * 部署态优先读运行时 window.AVERNET_RUNTIME_CONFIG.PUBLIC_BOT_BACKEND_ENABLED（布尔值），
 * 运行时显式设置优先于本编译常量；变量缺失（jest/SSR）按全栈默认开启。
 */
declare const TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED: boolean | undefined;
