import type { ChatMessage } from '@tc-chat/core';
import { useCallback, type Dispatch, type SetStateAction } from 'react';

/** Only server-confirmed, exactly correlated user messages may be removed. */
export function useCancelledGroupChatMessages(setMessages: Dispatch<SetStateAction<ChatMessage[]>>) {
  return useCallback((clientMessageIds: string[], canonicalMessageIds: string[]) => {
    const clientIds = new Set(clientMessageIds);
    const canonicalIds = new Set(canonicalMessageIds);
    setMessages((messages) => messages.filter((message) => {
      if (message.role !== 'user') return true;
      // Optimistic messages use the client key; hydrated history uses the canonical id.
      return !canonicalIds.has(message.id) && (typeof message.extra?.clientMessageId !== 'string'
        || !clientIds.has(message.extra.clientMessageId));
    }));
  }, [setMessages]);
}
