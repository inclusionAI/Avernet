import type { ChatMessage } from '@tc-chat/core';
import { useCallback, useEffect, useRef, type RefObject } from 'react';

/** 前置历史后返回原首条消息的直接前驱，避免视口跳到整批历史的最旧消息。 */
export function resolveHistoryPrependTargetId(previousIds: string[], nextIds: string[]): string | null {
  const previousAnchorId = previousIds[0];
  if (!previousAnchorId || nextIds[0] === previousAnchorId) return null;
  const anchorIndex = nextIds.indexOf(previousAnchorId);
  return anchorIndex > 0 ? nextIds[anchorIndex - 1] : null;
}

function getAnchorableMessageIds(messages: ChatMessage[]): string[] {
  return messages.filter((message) => message.role !== 'system').map((message) => message.id);
}

/** 单聊/群聊共用：加载前记住旧边界，前置完成后滚到旧边界的直接上一条。 */
export function useHistoryPrependAnchor(
  messages: ChatMessage[],
  rootRef: RefObject<HTMLDivElement | null>,
  onLoadMore?: () => void,
): (() => void) | undefined {
  const pendingIdsRef = useRef<string[] | null>(null);
  const handleLoadMore = useCallback(() => {
    if (!onLoadMore) return;
    pendingIdsRef.current = getAnchorableMessageIds(messages);
    onLoadMore();
  }, [messages, onLoadMore]);

  useEffect(() => {
    const previousIds = pendingIdsRef.current;
    if (!previousIds) return;
    const targetId = resolveHistoryPrependTargetId(previousIds, getAnchorableMessageIds(messages));
    if (!targetId) return;
    const frame = requestAnimationFrame(() => {
      const target = Array.from(rootRef.current?.querySelectorAll<HTMLElement>('[data-message-id]') ?? []).find(
        (element) => element.dataset.messageId === targetId,
      );
      const scrollElement = target?.parentElement?.parentElement;
      if (target && scrollElement) {
        scrollElement.scrollTop += target.getBoundingClientRect().top - scrollElement.getBoundingClientRect().top;
      }
      pendingIdsRef.current = null;
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, rootRef]);

  return onLoadMore ? handleLoadMore : undefined;
}
