import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import type { ChatMessage } from '@tc-chat/core';
import { useEffect, useState } from 'react';

/**
 * 消息区两态强制 hook（Spec: docs/specs/workspace-session-connection-display.md；预览反馈第五轮，
 * 用户拍板「简化为骨架屏 → 历史会话内容」）：可见消息为空时一律先显示骨架屏，仅当「已在线且
 * 消息持续为空超过 EMPTY_CONFIRM_MS」才确认空态（真·空会话的终态）——空态文案从「立即渲染」
 * 改为「确认后渲染」，历史装载与渲染之间的任何缝隙（setMessages 生效、占位过滤、WS 补齐、
 * BubbleList 挂载等）都被骨架屏吸收，物理上不再暴露「发送一条消息开始」中间态。
 *
 * 与就绪判定解耦：只看「可见消息空不空」与「在不在线」，不依赖状态机的任何中间路径。
 */
export const EMPTY_CONFIRM_MS = 1000;

export function useMessageAreaSkeleton({
  messages,
  status,
  confirmMs = EMPTY_CONFIRM_MS,
}: {
  /** 渲染口径的可见消息（调用方须与消息区渲染层共用同一过滤）。 */
  messages: ChatMessage[];
  /** 合成显示状态（connected 为进入流程完成的唯一在线终态）。 */
  status: ProviderConnectionStatus;
  /** 空态确认宽限（ms）。 */
  confirmMs?: number;
}): boolean {
  const isEmpty = messages.length === 0;
  const [emptyConfirmed, setEmptyConfirmed] = useState(false);
  const shouldConfirm = isEmpty && status === 'connected';

  useEffect(() => {
    // 条件破坏（消息到达 / 掉线）即撤销已确认的空态，重新进入骨架屏等待。
    if (!shouldConfirm) {
      if (emptyConfirmed) setEmptyConfirmed(false);
      return;
    }
    // 在线且消息为空：持续满宽限才确认空态（防历史装载缝隙闪现空态文案）。
    const timer = setTimeout(() => setEmptyConfirmed(true), confirmMs);
    return () => clearTimeout(timer);
  }, [shouldConfirm, confirmMs, emptyConfirmed]);

  // 内容显示的充分条件 = 可见消息非空 且 顶栏已连接（预览反馈第六轮：单聊历史经 HTTP 先到、
  // WS 连接后到时，骨架屏撑到连接完成——顶栏变绿与历史出现严格同帧，两链路视觉一致）。
  const contentReady = !isEmpty && status === 'connected';
  return !contentReady && !emptyConfirmed;
}
