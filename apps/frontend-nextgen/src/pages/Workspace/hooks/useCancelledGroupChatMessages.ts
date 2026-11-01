import type { ChatMessage } from '@tc-chat/core';
import { useCallback, type Dispatch, type SetStateAction } from 'react';

/** Only server-confirmed, exactly correlated user messages may be removed. */
export function useCancelledGroupChatMessages(setMessages: Dispatch<SetStateAction<ChatMessage[]>>) {
  return useCallback((clientMessageIds: string[]) => {
    const ids = new Set(clientMessageIds);
    setMessages((messages) => messages.filter((message) =>
      message.role !== 'user' || typeof message.extra?.clientMessageId !== 'string'
      || !ids.has(message.extra.clientMessageId),
    ));
  }, [setMessages]);
}
