/**
 * B16 — 公共核心单入口（公共化边界·原地版）。
 * ============================================================================
 * 本文件是"未来公共包出口面"的先行钉板：公共核心的对外符号一律由这里出。
 * 现状（原地版，不搬目录）：
 *   · 纯协议/生命周期文件（rpc/stream/rpcOnce/card/translate/cancelOutcomes/
 *     sendBan/choiceCache/urls/recovery/A2AProviderInput/sdkParser 类型段/
 *     v3FrameShapes/testing）零宿主耦合；
 *   · bootstrap.ts 尚含宿主装配（app 单例 + 开关缺省）——resolveA2aUrls/
 *     deriveEndpointBase/isLockCandidateError/isSendBannedForSession 与两常量
 *     虽是纯函数但地理上住在 bootstrap；公共化提取时它们随核心迁出，bootstrap
 *     瘦身为宿主 glue（消费方 import 面不变）。提取前经本入口引用它们是过渡
 *     姿态——宿主开关文件的窗口/构建期常量访问全部在调用期守卫内，import 无害。
 *
 * 宿主面（总开关函数/键、SDK 适配器、app 单例）**不在出口**——OCB 按 README
 * 消费契约自带开关面与 parserFactory。
 */
export { A2AProvider } from './A2AProvider';
// —— 协议底座 ——
export {
  buildRpcRequest, parseRpcMessage, classifyFailure,
  LOCK_CANDIDATE_KINDS,
  type RpcRequest, type RpcError, type ParsedRpc,
  type FailureKind, type WireFailure,
} from './rpc';
export { rpcOnce } from './rpcOnce';
export {
  streamRpc, MAX_PENDING_BYTES,
  type WireEvent,
} from './stream';
export {
  fetchCard, validateScenarios, Scenario,
  type ProbeResult, type ScenarioResult,
} from './card';
export {
  translateEvent,
  type TranslateKind, type TranslateOutput, type TranslateCtx,
} from './translate';
export {
  a2aUrlsFromWsUrl, a2aUrlsFromBaseUrl,
  type A2aUrls,
} from './urls';
// —— 生命周期与状态机 ——
export {
  TransportChoiceCache,
  type EntryKind, type ChoiceEntry,
} from './choiceCache';
export { SendBanRegistry, createSendBanRegistry } from './sendBan';
export {
  classifyCancel, CANCEL_UI_TEXT,
  type CancelOutcome, type CancelOutcomeKind, type CancelClassifyInput,
} from './cancelOutcomes';
export {
  REFUSAL_REASON_CACHE_LIMIT, REFUSAL_REASON_NOT_FOUND_OR_EXPIRED,
  DEFAULT_MAX_ATTEMPTS, DEFAULT_TOTAL_BUDGET_MS,
  DEFAULT_BACKOFF_CAPS_MS, DEFAULT_PER_CALL_TIMEOUT_MS,
  type RecoveryEngine, type RecoveryConfig, type RecoveryOutcome,
} from './recovery';
// —— parser 注入缝（类型段；宿主适配器 tcChatParser 不在出口）——
export {
  A2aParserFactoryMissingError,
  type SdkParser, type SdkParserFactory, type SdkParserConfig, type SdkParserCtor,
  type SdkParseResult, type SdkPendingApproval,
} from './sdkParser';
export type {
  A2aOpenClawInput, A2aOpenClawProviderConfig, A2aChatMessage,
} from './A2AProviderInput';
export type {
  A2AProviderConfig,
  A2aConnectionStatusEvent,
} from './A2AProvider';
// —— 过渡位：纯函数现住 bootstrap（提取时迁出，见文件头），import 期无害 ——
export {
  resolveA2aUrls, deriveEndpointBase, isLockCandidateError,
  isSendBannedForSession, A2A_CHOICE_SESSION_KEY, SEND_BAN_UI_TEXT,
} from './bootstrap';