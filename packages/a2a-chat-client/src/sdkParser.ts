/**
 * B10 — SDK parser 的结构契约（类型段）+ 注入缝类型。
 * ============================================================================
 * 【公共化边界（原地版）】本文件类型段是"公共核心"：零 SDK 运行时依赖、零
 * 浏览器窗口面/构建期全局触碰，将来公共化出仓时随纯协议文件一起走
 * （hostIsolation 钉板登记）。加载链已是宿主面：宿主适配器持有各自 SDK 的
 * 加载候选链；OCB 对位改造 = 换内部聊天 SDK 目标包，其余公共文件零改。
 */

import type { A2aOpenClawInput } from './A2AProviderInput';
import type { WireFailure } from './rpc';

export interface SdkParserConfig {
  enableThinking?: boolean;
  generateId?: () => string;
  xIAMToken?: string;
}

/** 聊天 SDK core ParseResult 的结构镜像（运行时依据 B8 钉板契约） */
export interface SdkParseResult {
  blocks: Record<string, any>[];
  content: string;
  done: boolean;
  toolCalls: Record<string, any>[];
  sessionKey?: string;
  isApproval: boolean;
  approvalRequest?: Record<string, any>;
  /** inject 消息独立作用域标识（runId='inject-*'） */
  runId?: string;
}

export interface SdkPendingApproval {
  approvalId: string;
  approvalData: Record<string, any>;
  approvalBlock: Record<string, any>;
  blockedMessages: Record<string, any>[];
}

/** OpenClawParser 公共面（OpenClawParser.d.ts: ProtocolParser 全成员）；
 *  OpenClawParser 的真面比消费面更大（hydrate/markAborted/buildInjectRequest
 *  等 A2AProvider 不消费，结构镜像按消费面收口） */
export interface SdkParser {
  onUpdate?: (result: SdkParseResult) => void;
  onComplete?: (result: SdkParseResult) => void;
  onError?: (error: Error) => void;
  getMessageId(): string;
  getResult(): SdkParseResult;
  reset(): void;
  handleMessage(message: Record<string, any>): boolean;
  buildRequest(params: A2aOpenClawInput): Record<string, any>;
  updateApprovalStatus(approvalId: string, status: 'approved' | 'rejected'): boolean;
  clearPendingApproval(): Record<string, any>[];
  getPendingApproval(): SdkPendingApproval | null;
  setSessionKey(sessionKey: string): void;
}

export type SdkParserCtor = new (config?: SdkParserConfig) => SdkParser;

/**
 * 宿主注入缝（公共/宿主解耦核心）：宿主提供"构造好的 parser 实例工厂"——
 * ctor 身份、加载策略、构造差异全部融化在宿主适配器里（nextgen 的开源 SDK
 * 适配与 OCB 的内部聊天 SDK 适配各持一份）。同步返回或异步加载
 * 均可，配 parserReady 的既有 await 语义。
 */
export type SdkParserFactory =
  (config: SdkParserConfig) => Promise<SdkParser> | SdkParser;

/** 缺注入的 typed 报错（js 调用方空洞兜底）：无手搓兜底 parser（B8 钉板：
 * 真 parser 就是 oracle）——ensureParser 面（connect/request/恢复/审批全部
 * 入口）以 .failure={kind:'protocol',reason:'parser_factory_missing'} 上浮。 */
export class A2aParserFactoryMissingError extends Error {
  readonly failure: WireFailure;
  constructor() {
    super('a2a parserFactory is required: host must inject an SDK parser factory');
    this.name = 'A2aParserFactoryMissingError';
    this.failure = { kind: 'protocol', reason: 'parser_factory_missing' };
  }
}