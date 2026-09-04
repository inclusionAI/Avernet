/**
 * 服务器地址配置（Open Core 占位默认）。
 *
 * 本文件只保存可公开的 localhost 占位地址，不写内部域名。
 * 真实内部网关地址由 config/internal/servers.ts 或环境变量注入。
 */
export interface ServerConfig {
  /** TeamClaw Gateway，承载 /openapi/v1/** 后端接口（协作/工坊/市场等）。 */
  TEAMCLAW_GW: string;
  /** Open Core BCS endpoint：注册 CLI 在 PRE/PROD 环境下发使用的接入地址。 */
  BCS_ENDPOINT_PRE: string;
  BCS_ENDPOINT_PROD: string;
  /** Admin 后端（clawweb/空间中台），承载 /openapi/v1/spaces、/work-orders、/work-order-notifications。
   * 与 TeamClaw Gateway 不同域，独立代理；真实内部地址由 config/internal/servers.ts 注入。Open Core 占位 localhost。 */
  TEAMCLAW_ADMIN: string;
  /** PrivateChat 管理 API，承载 /api/v1/expert-chats/**。 */
  PRIVATE_CHAT_MANAGEMENT: string;
  /** 旧版 AgentCoding /api/**，本地开发通过同源代理转发到旧 AgentClaw。 */
  LEGACY_AGENTCLAW?: string;
  /** PrivateChat 会话代理，承载 /proxypass/** 与 WebSocket。 */
  PRIVATE_CHAT_SESSION: string;
  /** AIX Harness / 模板工厂，承载 /template-factory/**（可选，缺失时回退 localhost:8888）。 */
  AIXHARNESS?: string;
  /** ASF aixcore API，承载 /aixcore/** 系统接口（可选，缺失时回退 localhost:8888）。 */
  AIXCORE?: string;
  /** Clawweb 后端，承载 workflow 列表/详情 GET /api/workflows(/{id})。
   * 真实内部地址由 config/internal/servers.ts 注入；Open Core 占位 localhost。 */
  CLAWWEB: string;
  /** MCP Center（MCP 详情页办公网权限申请状态 auth/tools 直连），经 /mcpcenter 前缀代理、
   * pathRewrite 去前缀（可选，缺失时回退 localhost:8888）。Internal Only。 */
  MCPCENTER?: string;
}

/** 全环境服务器配置 map。 */
export type ServerConfigMap = Record<'LOCAL' | 'DEV' | 'PRE' | 'PROD', ServerConfig>;

const PLACEHOLDER: ServerConfig = {
  TEAMCLAW_GW: 'http://127.0.0.1:8888',
  BCS_ENDPOINT_PRE: 'http://127.0.0.1:21000',
  BCS_ENDPOINT_PROD: 'http://127.0.0.1:21000',
  TEAMCLAW_ADMIN: 'http://127.0.0.1:8888',
  PRIVATE_CHAT_MANAGEMENT: 'http://127.0.0.1:8888',
  LEGACY_AGENTCLAW: 'http://127.0.0.1:8888',
  PRIVATE_CHAT_SESSION: 'http://127.0.0.1:8889',
  AIXCORE: 'http://127.0.0.1:8888',
  CLAWWEB: 'http://127.0.0.1:8888',
};

export const SERVERS: ServerConfigMap = {
  LOCAL: { ...PLACEHOLDER },
  DEV: { ...PLACEHOLDER },
  PRE: { ...PLACEHOLDER },
  PROD: { ...PLACEHOLDER },
};

export type EnvName = keyof ServerConfigMap;
