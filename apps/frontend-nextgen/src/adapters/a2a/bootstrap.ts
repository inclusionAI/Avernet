/**
 * B13 — A2AProvider 组装工厂 / A2A 传输选择唯一编排点（spec stage2 §2 / §3.1）。
 * ============================================================================
 *
 * 双入口分工（useSessions 契约事实：createProvider 必须同步返回 ChatProvider，
 * 见 adapters/dist/hooks/useSessions.d.ts——探测是异步的，工厂是同步的）：
 *   · maybeBuildA2AProvider（异步正路）：card 探测（无 token、5s 超时上限）
 *     → 分场景能力校验 → TransportChoiceCache 收敛 → 构造 A2AProvider。
 *   · takeCachedA2AProvider（同步捷径）：会话工厂从缓存直取结论；缓存未决/
 *     probe-v3 时 fire-and-forget 后台预热（probe-v3 可翻案，重探测只此触发）
 *     然后本轮返回 null 落回 v3。已决 'a2a' 的键同步换入 A2AProvider。
 *
 * 【关键决策：选择缓存键 = 页级 app 决策（A2A_CHOICE_SESSION_KEY）】
 * spec §3.3 缓存键形式是 (sessionKey, endpointBase)，但探测产物（card 卡 + 场景
 * 能力）是 **endpoint 级事实**；而 useSessions 对每个 sessionKey 只在首次建 Chat
 * 时调用一次 createProvider——per-session 键会让每个新会话必然 miss → v3，
 * 实践中令 a2a 决策几乎不可达。故用稳定键 `default` 承载页级传输选择
 * （endpointBase 仍是键成员）；context 级运行期状态（send-ban / 恢复锚）走
 * 各自的真实 sessionKey 独立面，不受此决策影响。sessionKey 保留注入口供后续
 * 按会话分叉。
 *
 * 【锁定的唯一来源 = 运行期硬失败】(spec §3.3 窄定义)：构造出的 provider 的
 * connection-status 订阅——A2AProvider 只在 wire 级失败时发 status:'error'（携带
 * 原始 typed failure，见 notifyHardFailure）；本模块按 LOCK_CANDIDATE_KINDS
 * (auth/protocol) 收敛为 markLocked。探测失败与场景缺项一律 probe-v3（可由后续
 * 探测翻案），**永不**因探测锁定；transport/rpc 级失败走 B11 恢复正路不锁。
 *
 * 【HITL 是启用前置条件】(spec §3 #4 / §5)：普通聊天可中途触发审批——能力
 * 校验按"该会话可能产生的交互"预判，不得等审批出现才改判。card 缺 resolve
 * 声明 → 发送前留 v3（可与 stream+recovery 同判 probe-v3，永不锁）。运行期
 * 出现"声明了但 resolve_unsupported"由 A2AProvider 审批面 typed 拒绝上浮
 * （§5 失败面兜底）——它不替代发送前的传输选择。
 *
 * 【已知边界（页级缓存命中率先行）】首个会话在探测落定前构造必然落 v3；
 * 探测落定后新开会话（Chat 新建/重建）即走 A2A。页面刷新重新探测（缓存不持久化）。
 */
import { A2AProvider } from './A2AProvider';
import { TransportChoiceCache } from './choiceCache';
import { fetchCard, Scenario, validateScenarios } from './card';
import { createSendBanRegistry, type SendBanRegistry } from './sendBan';
import { isA2aClientEnabled } from './switch';
import { createTcChatParserFactory } from './tcChatParser';
// ─── 公共策略纯函数与类型（policy.ts 单源；此处 re-export 保消费面） ──────

// review R2-4：这组成员曾是本文件内联定义——index.ts 经 re-export 转出时把
// switch/tcChatParser/app 单例拖进公共依赖图。外提到 policy.ts 后本文件只剩
// 宿主装配；既有消费方（botChatProvider/useBotChat/测试）import 面不变。
export {
  resolveA2aUrls, deriveEndpointBase, isLockCandidateError, isSessionBanned,
  A2A_CHOICE_SESSION_KEY, SEND_BAN_UI_TEXT,
  type A2aBootstrapConfig,
} from './policy';
import {
  resolveA2aUrls, isSessionBanned, isLockCandidateError,
  A2A_CHOICE_SESSION_KEY,
  type A2aBootstrapConfig,
} from './policy';
import type { A2aUrls } from './urls';

/** 测试 seam / 组合注入口（全部可选；缺省 = app 单例 + 全局 fetch + 总开关） */
export interface A2aBootstrapDeps {
  fetchImpl?: typeof fetch;
  choiceCache?: TransportChoiceCache;
  switchOn?: () => boolean;
  /** 选择缓存会话键（缺省页级 'default'，见文件头决策） */
  sessionKey?: string;
}

// ─── app 级单例（context 级生存期：禁令/决策跨 provider 换代存活） ─────────

/** 页级传输选择缓存（不持久化：刷新即重探，choiceCache.ts 契约） */
const appChoiceCache = new TransportChoiceCache();
const appSendBan = createSendBanRegistry();

/**
 * spec §3.1 send-ban 注册表的 app 级唯一实例：禁令是 context 级状态、与
 * provider 实例生命周期无关（provider 换代/重建后同 (endpoint, context) 仍
 * 禁）。构造出的 A2AProvider 统一注入此实例（A2AProvider 缺省者是模块私有的
 * 另一份——不经 bootstrap 的构造路径）。sendMessage 前置门同源消费。
 */
export function getSendBanRegistry(): SendBanRegistry {
  return appSendBan;
}

// ─── 构造 + 运行期锁定订阅 ────────────────────────────────────────────────

interface BuildCtx {
  /** 锁定写入的选择缓存与键位（与决策缓存同键，locked 吸收后续 decide(true)） */
  choiceCache: TransportChoiceCache;
  sessionKey: string;
  endpointBase: string;
}

function buildA2AProvider(
  providerConfig: A2aBootstrapConfig,
  urls: A2aUrls,
  deps: A2aBootstrapDeps,
  ctx: BuildCtx,
): A2AProvider {
  const provider = new A2AProvider({
    ...providerConfig,
    ...(deps.fetchImpl ? { fetchImpl: deps.fetchImpl } : {}),
    a2aUrls: urls,
    // parserFactory：nextgen 宿主适配（@tc-chat 加载链在 tcChatParser.ts，
    // 公共注入缝的本地实现；OCB 消费时替换为其内部 SDK 同形工厂）
    parserFactory: createTcChatParserFactory(),
    // app 级单例注入：禁令与发送门/本模块 getSendBanRegistry() 同源
    sendBan: getSendBanRegistry(),
  });
  // 运行期锁定订阅（唯一 markLocked 位）：wire 级 typed 硬失败
  // （auth/protocol，A2AProvider.notifyHardFailure 播报）→ locked-v3。
  provider.subscribeToConnectionStatus((event) => {
    if (event.status === 'error' && isLockCandidateError(event.error)) {
      ctx.choiceCache.markLocked(ctx.sessionKey, ctx.endpointBase);
    }
  });
  return provider;
}

// ─── 异步正路：探测 → 校验 → 收敛 → 构造 ─────────────────────────────────

/**
 * A2A 传输选择六态决策（决策表钉死在 __tests__/bootstrap.test.ts）：
 *   开关关 → null（缓存不动 + 零 fetch）；
 *   非 ws url 且无 a2aBaseUrl → null（不落缓存——url 形状不是 endpoint
 *   的探测结论；B17 起服务端签发的 a2aBaseUrl 可豁免：签发即权威形状）；
 *   card 探测失败 → decide(false)=probe-v3 + null（可翻案，永不锁）；
 *   场景缺项（STREAM/RECOVERY/HITL 任一不过）→ probe-v3 + null；
 *   已 locked → 探测照跑但被锁吸收（decide 尊重 locked），null；
 *   全过 → decide(true)，返回注入 sendBan/锁定订阅的 A2AProvider。
 * HITL 是启用前置条件之一（spec §3 #4，依据见文件头）。
 */
export async function maybeBuildA2AProvider(
  providerConfig: A2aBootstrapConfig,
  deps: A2aBootstrapDeps = {},
): Promise<A2AProvider | null> {
  const switchOn = deps.switchOn ?? isA2aClientEnabled;
  const choiceCache = deps.choiceCache ?? appChoiceCache;
  const sessionKey = deps.sessionKey ?? A2A_CHOICE_SESSION_KEY;

  if (!switchOn()) return null;                 // 缓存不动 + 零 fetch
  let urls: A2aUrls;
  try {
    urls = resolveA2aUrls(providerConfig);
  } catch {
    return null;
  }

  const probe = await fetchCard(
    urls.cardUrl, deps.fetchImpl ?? fetch, { timeoutMs: 5000 });
  if (!probe.card) {
    choiceCache.decide(sessionKey, urls.rpcUrl, false);   // probe-v3（可翻案）
    return null;
  }
  const failed = validateScenarios(
    probe.card, [Scenario.STREAM, Scenario.RECOVERY, Scenario.HITL],
  ).filter((r) => !r.ok);
  if (failed.length > 0) {
    // 场景缺项是 probe-v3 面（spec §3.3：locked 只来自运行期硬失败）
    choiceCache.decide(sessionKey, urls.rpcUrl, false);
    return null;
  }
  const entry = choiceCache.decide(sessionKey, urls.rpcUrl, true);
  if (entry.kind === 'locked-v3') return null;   // 锁高于探测（decide 已尊重）
  return buildA2AProvider(providerConfig, urls, deps, {
    choiceCache, sessionKey, endpointBase: urls.rpcUrl,
  });
}

// ─── 同步捷径：缓存直取 + 后台预热 ────────────────────────────────────────

/** 预热在飞去重：同 (cache, sessionKey::endpoint) 只一发，探测挂死不叠加 */
const inflightPrewarms =
  new WeakMap<TransportChoiceCache, Map<string, Promise<null>>>();

function prewarmA2AChoice(
  providerConfig: A2aBootstrapConfig, deps: A2aBootstrapDeps, urls: A2aUrls,
): void {
  const choiceCache = deps.choiceCache ?? appChoiceCache;
  const sessionKey = deps.sessionKey ?? A2A_CHOICE_SESSION_KEY;
  const key = `${sessionKey}::${urls.rpcUrl}`;
  let flights = inflightPrewarms.get(choiceCache);
  if (flights === undefined) {
    flights = new Map();
    inflightPrewarms.set(choiceCache, flights);
  }
  if (flights.has(key)) return;
  const slot = flights;
  const run = maybeBuildA2AProvider(providerConfig, deps)
    .then(() => null, () => null);            // 探测异常不外溢（fire-and-forget）
  slot.set(key, run);
  void run.then(() => { slot.delete(key); }); // 台账自清（run 已吸收 rejection）
}

/**
 * 同步会话工厂入口（useMultiSessionChat 唯一调用的面）：
 *   开关关 / 非 ws url（无服务端 a2aBaseUrl）→ null；
 *   缓存未决 / probe-v3 → 触发后台预热（probe-v3 即重探测，锁型不预热）→ null；
 *   locked-v3 → null（页内最高优先级，自动清除无效——不预热不翻案）；
 *   a2a → 同步构造返回（信任页级已决结论；connect 期探测失败由 onError 面上浮）。
 */
export function takeCachedA2AProvider(
  providerConfig: A2aBootstrapConfig,
  deps: A2aBootstrapDeps = {},
): A2AProvider | null {
  const switchOn = deps.switchOn ?? isA2aClientEnabled;
  const choiceCache = deps.choiceCache ?? appChoiceCache;
  const sessionKey = deps.sessionKey ?? A2A_CHOICE_SESSION_KEY;

  if (!switchOn()) return null;
  let urls: A2aUrls;
  try {
    urls = resolveA2aUrls(providerConfig);
  } catch {
    return null;
  }
  const entry = choiceCache.get(sessionKey, urls.rpcUrl);
  if (entry === undefined || entry.kind === 'probe-v3') {
    prewarmA2AChoice(providerConfig, deps, urls);
    return null;
  }
  if (entry.kind === 'locked-v3') return null;
  return buildA2AProvider(providerConfig, urls, deps, {
    choiceCache, sessionKey, endpointBase: urls.rpcUrl,
  });
}

// ─── §3.1 send-ban 消费门（宿主便利封装；判据纯函数在 policy.ts） ────────

/**
 * 发送门（hook 侧入口）：isSessionBanned 的 nextgen 便利封装——registry 缺省
 * app 单例（判据/键位口径与文档见 policy.isSessionBanned；语义注释随真身迁去，
 * 此处不在公共面——公共入口导出显式收 registry 的纯判据版）。
 */
export function isSendBannedForSession(
  sessionKey: string,
  wsUrl: string,
  a2aBaseUrl?: string,
  registry: SendBanRegistry = appSendBan,
): boolean {
  return isSessionBanned(registry, sessionKey, wsUrl, a2aBaseUrl);
}

// A2A M2 bootstrap seam: 取消结果 UI 文案单源转出（spec §4.3 表）——
// useMultiSessionChat 的 toast 消费与 cancelOutcomes 保持单一来源。
export { CANCEL_UI_TEXT } from './cancelOutcomes';
