/**
 * B16 — 公共核心单入口（公共化边界·原地版）。
 * ============================================================================
 * 本文件是"未来公共包出口面"的先行钉板：公共核心的对外符号一律由这里出。
 * 依赖图闭合（review R2-4）：本入口的静态 import 图**只**覆盖公共核心文件
 *（hostIsolation 的传递图检查钉死）——不达 bootstrap/switch/tcChatParser，
 * 因此 OCB 经此入口消费时既不会解析宿主 SDK 的动态注入位、也不会引入
 * nextgen 的 app 单例。传输选择策略纯函数在 policy.ts（宿主 bootstrap 保
 * 有它们的历史 re-export 以维持消费面）。
 *
 * 宿主面（总开关函数/键、SDK 适配器、app 单例、isSendBannedForSession 便利
 * 封装）**不在出口**——OCB 按 README 消费契约自带开关面与 parserFactory；
 * 发送门判据出口是显式收 registry 的 isSessionBanned 纯函数版。
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
// —— 传输选择策略纯函数（policy.ts 单源；宿主 bootstrap 仅保历史 re-export）——
export {
  resolveA2aUrls, deriveEndpointBase, isLockCandidateError, isSessionBanned,
  A2A_CHOICE_SESSION_KEY, SEND_BAN_UI_TEXT,
  type A2aBootstrapConfig,
} from './policy';