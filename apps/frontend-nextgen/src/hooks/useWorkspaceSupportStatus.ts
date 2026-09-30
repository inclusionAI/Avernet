import { useSessionDisplayStatus, type SessionEnterOutcome } from '@/pages/Workspace/hooks/useSessionDisplayStatus';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { useCallback, useEffect, useState } from 'react';

/**
 * 客服（support）会话显示状态合成（Spec: docs/specs/workspace-session-connection-display.md AC-7）——
 * 从 useWorkspace 拆出以控制文件体积，行为不变。
 *
 * 就绪口径「连接即就绪」：客服为单固定会话，历史经 SDK defaultMessages 独立拉取、不进顶栏文案
 * （与改造前显示口径一致）。rawStatus='connected' 为兜底成功证据——覆盖首次 connect、手动重连与
 * SDK 自动重连，防止「重连成功后仍显示连接失败 / 卡在连接中」。connect effect 开始/失败时分别
 * 调 markEnterStarted / markEnterFailed 回写信号。
 */
export function useWorkspaceSupportStatus({
  isSupportTarget,
  targetId,
  rawStatus,
}: {
  isSupportTarget: boolean;
  targetId: string | null;
  rawStatus: ProviderConnectionStatus;
}) {
  const [connectDone, setConnectDone] = useState(false);
  const [enterFailed, setEnterFailed] = useState(false);

  // 连接成功兜底：raw connected 即视为进入就绪并清除失败标记。
  useEffect(() => {
    if (rawStatus !== 'connected') return;
    if (!connectDone) setConnectDone(true);
    if (enterFailed) setEnterFailed(false);
  }, [rawStatus, connectDone, enterFailed]);

  const markEnterStarted = useCallback(() => {
    setConnectDone(false);
    setEnterFailed(false);
  }, []);
  const markEnterFailed = useCallback(() => setEnterFailed(true), []);

  const outcome: SessionEnterOutcome = enterFailed ? 'failed' : connectDone ? 'ready' : 'pending';
  const display = useSessionDisplayStatus({
    sessionKey: isSupportTarget ? targetId : null,
    rawStatus,
    enterOutcome: outcome,
  });
  return { status: display.status, isEntering: display.isEntering, markEnterStarted, markEnterFailed };
}
