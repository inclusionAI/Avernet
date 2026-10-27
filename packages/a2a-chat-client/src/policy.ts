/**
 * B16 — 传输选择策略纯函数集（公共核心）。
 * ============================================================================
 * 从 bootstrap.ts 外提（review R2-4：公共入口 index.ts 此前经 bootstrap 的
 * re-export 传递引入宿主装配面的依赖——"公共核心宿主无关"必须在模块图层面
 * 成立，不是单文件 grep 层面）。bootstrap 瘦身后仅保宿主装配（app 单例 +
 * 开关注入 + 工厂组装），并从这里 re-export 维持既有消费面。
 *
 * 公共化边界：本文件属公共核心（hostIsolation 钉板 + 传递图检查双覆盖）。
 */

import type { A2aOpenClawProviderConfig } from './A2AProviderInput';
import { a2aUrlsFromBaseUrl, a2aUrlsFromWsUrl, type A2aUrls } from './urls';
import { LOCK_CANDIDATE_KINDS, type WireFailure } from './rpc';
import type { SendBanRegistry } from './sendBan';

// ─── 输入类型（结构镜像，零 SDK 依赖——同 A2AProviderInput.ts 理由） ────────

/** v3 providerConfig 的子集 + A2A 消费面成员（url 必填，其余可选透传） */
export interface A2aBootstrapConfig extends A2aOpenClawProviderConfig {
  /** 动态 IAM 凭证（每次 request/RPC 前取新，覆盖静态 xIAMToken） */
  iamTokenGetter?: () => Promise<string | undefined>;
  /**
   * B17 服务端签发的 A2A base（连接响应 sockets kind='a2a' 的 url，由 BaaS
   * 沙箱层拼成浏览器可达形态）。在场即压过从 ws url 的推导——推导是站点
   * 直连拓扑的兜底，网关 relay topo 下派生地址路由不到 card（404）。
   */
  a2aBaseUrl?: string;
  /**
   * 服务端明确拒绝本用户的 A2A 访问（用户灰度未命中，连接响应
   * a2a_withheld=true）。与"没有 a2aBaseUrl"是两个语义：缺席允许推导
   * 兜底（旧服务端/直连拓扑）；refusal 禁止推导、探测与缓存命中构造，
   * 稳定落 v3。注意它**不**影响 send-ban 遗留键的推导查询——回滚换轨
   * 在途任务的发送禁令必须继续生效。
   */
  a2aWithheld?: boolean;
}

// ─── 常量（UI/键位单源） ──────────────────────────────────────────────────

/** 选择缓存的页级会话键（决策见 bootstrap 文件头） */
export const A2A_CHOICE_SESSION_KEY = 'default';

/** §3.1 send-ban 的 UI 门文案（spec 原文；唯一来源，hook 侧勿另抄） */
export const SEND_BAN_UI_TEXT = '该会话暂不可发送（底层执行停止未确认）';

// ─── 纯函数 ───────────────────────────────────────────────────────────────

/**
 * A2A 地址解析（两条正路，失败上抛——形状问题不是 endpoint 的探测结论）：
 * 服务端签发 a2aBaseUrl（权威）优先，否则回退 v3 ws url 直连推导。
 */
export function resolveA2aUrls(providerConfig: A2aBootstrapConfig): A2aUrls {
  return providerConfig.a2aBaseUrl
    ? a2aUrlsFromBaseUrl(providerConfig.a2aBaseUrl)
    : a2aUrlsFromWsUrl(providerConfig.url);
}

/** 锁定候选分类（wire 级 typed 硬失败 auth/protocol 才 lock，transport 不锁） */
export function isLockCandidateError(err: unknown): boolean {
  const failure = (err as { failure?: WireFailure } | null | undefined)?.failure;
  return failure !== undefined && LOCK_CANDIDATE_KINDS.has(failure.kind);
}

/** ws url → A2A endpointBase（rpc url）；非 ws url → null */
export function deriveEndpointBase(wsUrl: string): string | null {
  try {
    return a2aUrlsFromWsUrl(wsUrl).rpcUrl;
  } catch {
    return null;
  }
}

/**
 * 发送门判据（§3.1）：(endpointBase, sessionKey) 命中禁令 → 拦截本次发送。
 * **registry 必填**（公共面不默认宿主单例——review R2-4）；宿主便利封装
 *（isSendBannedForSession，缺省 app 单例）留在 bootstrap。
 */
export function isSessionBanned(
  registry: SendBanRegistry,
  sessionKey: string,
  wsUrl: string,
  a2aBaseUrl?: string,
): boolean {
  let base: string | null;
  try {
    base = resolveA2aUrls({ url: wsUrl, a2aBaseUrl }).rpcUrl;
  } catch {
    base = null;
  }
  return base !== null && registry.isBanned(base, sessionKey);
}