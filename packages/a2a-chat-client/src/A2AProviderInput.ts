/**
 * B10 — A2AProvider 的对 SDK 配套类型的结构镜像。
 * ============================================================================
 * 为什么不直接 import 聊天 SDK 适配器包的类型：A2A 模块零 SDK 运行时依赖
 * （parser 由宿主经注入缝提供），类型直依赖会把 SDK 类型面固化进
 * 本模块的编译闭包——SDK 升 minor 改 d.ts 时错误劈面全树。SDK 的 d.ts 形状
 * 在此结构化镜像（字段与官方 OpenClawProvider.d.ts 的 2.1.0 档案逐一对齐，
 * 缺省性与 d.ts 一致）；新字段在真 app 构建下经结构化兼容自然互通。
 * 宿主字样禁入公共文件（hostIsolation 钉板）——包名级档案见 testing 契约注。
 */

/** OpenClawProvider.d.ts:3-15 OpenClawInput 镜像 */
export interface A2aOpenClawInput {
  query: string;
  sessionKey?: string;
  messages?: Array<{ role: string; content: string }>;
  /** 消息附件（图片等） */
  attachments?: Record<string, any>[];
  /** 是否为 inject 类型消息 */
  isInject?: boolean;
  [key: string]: any;
}

/** OpenClawProviderConfig.d.ts:28-97（大字段面前略；A2AProvider 实际消费面 + 保真透传）镜像 */
export interface A2aOpenClawProviderConfig {
  /** v3 是 ws url；A2A 面由 a2aUrls 派生/直配（见 A2aUrls），此字段保留兼容基类 */
  url: string;
  authToken?: string;
  /** x-iam-token（IAM 认证） */
  xIAMToken?: string;
  userId?: string;
  displayName?: string;
  xProxypassToken?: string;
  sessionKey?: string;
  /** 思考标签开关（透传 parser config.enableThinking） */
  enableThinkingTag?: boolean;
  /** 错误消息内容 - 请求失败时显示 */
  fallbackMessage?: string;
  [key: string]: any;
}

/** 聊天 SDK core 的 ChatMessage 结构镜像（Provider 输出面） */
export interface A2aChatMessage {
  id: string;
  role: string;
  content: string;
  blocks?: Record<string, any>[];
  status?: string;
  sessionKey?: string;
  extra?: Record<string, any>;
}