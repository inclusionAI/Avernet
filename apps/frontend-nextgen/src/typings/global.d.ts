declare module '*.less';

/** 仅 PRESET=local 注入；非本地构建固定为空字符串。 */
declare const TEAMCLAW_OPENAPI_USER_ID: string;

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
