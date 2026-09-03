import { useCallback, useEffect, useState } from 'react';

import type { ChatMessage } from '@tc-chat/core';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import { getMessageText } from './useMessageInteractions';

interface UseMessageEditOptions {
  sessionId: string | null | undefined;
  isRequesting: boolean;
  onDraftChange: (content: string) => void;
  inputRef?: { current: SenderRef | null };
}

export function useMessageEdit({ sessionId, isRequesting, onDraftChange, inputRef }: UseMessageEditOptions) {
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);

  useEffect(() => {
    setEditingMessageId(null);
  }, [sessionId]);

  const editMessage = useCallback(
    (message: ChatMessage) => {
      const messageText = getMessageText(message);
      if (!messageText.trim() || isRequesting) return;
      onDraftChange(messageText);
      inputRef?.current?.clear();
      inputRef?.current?.insert(messageText);
      inputRef?.current?.focus();
      setEditingMessageId(message.id);
    },
    [inputRef, isRequesting, onDraftChange],
  );

  const cancelEdit = useCallback(() => {
    setEditingMessageId(null);
    onDraftChange('');
    inputRef?.current?.clear();
  }, [inputRef, onDraftChange]);

  const finishEdit = useCallback(() => {
    setEditingMessageId(null);
  }, []);

  return { editingMessageId, editMessage, cancelEdit, finishEdit };
}
