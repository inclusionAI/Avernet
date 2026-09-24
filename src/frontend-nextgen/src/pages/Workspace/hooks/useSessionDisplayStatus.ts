import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { useEffect, useState } from 'react';

/**
 * 会话连接显示状态合成层（Spec: docs/specs/workspace-session-connection-display.md）。
 *
 * 把「WS 原始连接状态 + 进入流程结果 + 会话代次 + 自动重连接管标记」合成为用户可理解的
 * 五值显示语义（connecting / connected / reconnecting / disconnected / error），供 UI
 * 文案与骨架屏统一消费，消灭切换会话期间由实现细节（拉凭证 / 握手 / 拉历史）引起的
 * 中间态跳变。
 *
 * 合成规则（优先级自上而下）：
 * 1. 进入失败（enterOutcome === 'failed'）→ 一律显示 error（连接失败详情由各链路 phase/错误通道另行透出）；
 * 2. 自动重连接管（autoReconnecting，意外掉线后的前端层退避重连）→ 显示 reconnecting；
 * 3. 本会话已就绪（曾达到 enterOutcome === 'ready'）→ 透传 rawStatus：
 *    - connected / reconnecting / connecting / error 立即透传；
 *    - disconnected 在宽限期（默认 5s，承接 useConnectionStatusSmoothing 语义）内先显示 connecting，
 *      宽限期后仍断开才降级 disconnected——避免静默断开误报「已断开」；
 * 4. 无会话（sessionKey === null）→ 同就绪态透传（含宽限）；
 * 5. 进入窗口（会话已选中、尚未就绪、未失败）→ 一律 connecting：吞掉一切中间值，
 *    含上一会话残留的旧状态与本会话 WS 提前发出的 connected（历史未装完不显示「已连接」）。
 *
 * 就绪记录 once 语义：enterOutcome 达到 ready 即记录 sessionKey，此后进入信号回落
 * （如同会话刷新历史 nonce 重拉）不回退显示，避免「已连接 ↔ 连接中」闪烁。
 */
export type SessionEnterOutcome = 'pending' | 'ready' | 'failed';

export interface UseSessionDisplayStatusInput {
  /** 当前会话标识（如 sessionId）；null 表示无选中会话。变化即开启新的进入窗口。 */
  sessionKey: string | null;
  /** WS 原始连接状态（来自 provider.subscribeToConnectionStatus）。 */
  rawStatus: ProviderConnectionStatus;
  /** 进入流程结果：pending=进行中；ready=连接与历史均完成；failed=任一步明确失败。 */
  enterOutcome: SessionEnterOutcome;
  /** 意外掉线后前端层自动重连接管标记（群聊 useGroupChatAutoReconnect）。 */
  autoReconnecting?: boolean;
  /** disconnected 宽限期（ms）。 */
  graceMs?: number;
}

export interface SessionDisplayStatusResult {
  /** 合成后的显示状态：UI 文案映射的唯一输入。 */
  status: ProviderConnectionStatus;
  /** 是否处于进入窗口：会话已选中但未就绪且未失败。消息区骨架屏判定用（就绪后掉线宽限不再出骨架屏）。 */
  isEntering: boolean;
}

const DEFAULT_GRACE_MS = 5000;

export function useSessionDisplayStatus({
  sessionKey,
  rawStatus,
  enterOutcome,
  autoReconnecting = false,
  graceMs = DEFAULT_GRACE_MS,
}: UseSessionDisplayStatusInput): SessionDisplayStatusResult {
  const [readySession, setReadySession] = useState<string | null>(null);
  const [graceExpired, setGraceExpired] = useState(false);

  // 就绪记录：进入流程达到 ready 即绑定 sessionKey（once 语义，回落不回退）。
  useEffect(() => {
    if (sessionKey !== null && enterOutcome === 'ready') {
      setReadySession(sessionKey);
    }
  }, [sessionKey, enterOutcome]);

  const isSessionReady = sessionKey !== null && readySession === sessionKey;
  // 透传 + 宽限分支：已就绪，或无会话。
  const passthrough = isSessionReady || sessionKey === null;

  // disconnected 宽限计时：仅透传分支读 graceExpired；离开 disconnected 或退出透传分支即复位。
  // 复位依赖（rawStatus / passthrough）覆盖会话切换场景——切新会话必经进入窗口（passthrough=false），
  // 宽限期随新会话从头计。
  useEffect(() => {
    if (rawStatus !== 'disconnected' || !passthrough) {
      setGraceExpired(false);
      return;
    }
    const timer = setTimeout(() => setGraceExpired(true), graceMs);
    return () => clearTimeout(timer);
  }, [rawStatus, passthrough, graceMs]);

  let status: ProviderConnectionStatus;
  if (enterOutcome === 'failed') {
    status = 'error';
  } else if (autoReconnecting) {
    status = 'reconnecting';
  } else if (passthrough) {
    status = rawStatus === 'disconnected' && !graceExpired ? 'connecting' : rawStatus;
  } else {
    status = 'connecting';
  }

  const isEntering = sessionKey !== null && enterOutcome !== 'failed' && !isSessionReady;

  return { status, isEntering };
}

export { DEFAULT_GRACE_MS };
