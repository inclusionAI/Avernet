/** @jest-environment jsdom */
import { useCancelledGroupChatMessages } from '@/pages/Workspace/hooks/useCancelledGroupChatMessages';
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from '@jest/globals';
import type { ChatMessage } from '@tc-chat/core';
import { useState } from 'react';

describe('useCancelledGroupChatMessages', () => {
  it('按关联 ID 删除用户消息，不按重复内容匹配，不删除 assistant 或无关联的历史', () => {
    const initial = [
      { id: 'local-1', role: 'user', content: 'same', extra: { clientMessageId: 'client-1' } },
      { id: 'local-2', role: 'user', content: 'same', extra: { clientMessageId: 'client-2' } },
      { id: 'reply', role: 'assistant', extra: { clientMessageId: 'client-1' } },
      { id: 'history', role: 'user', content: 'same' },
    ] as ChatMessage[];
    const { result } = renderHook(() => {
      const [messages, setMessages] = useState(initial);
      return { messages, cancel: useCancelledGroupChatMessages(setMessages) };
    });
    act(() => result.current.cancel(['client-1'], []));
    expect(result.current.messages.map((m) => m.id)).toEqual(['local-2', 'reply', 'history']);
    act(() => result.current.cancel(['missing'], []));
    expect(result.current.messages.map((m) => m.id)).toEqual(['local-2', 'reply', 'history']);
  });

  it('matches canonical history IDs and client correlations without deleting assistant messages', () => {
    const initial = [
      { id: 'canonical-1', role: 'user', content: 'same', status: 'history' },
      { id: 'canonical-2', role: 'user', content: 'same', status: 'history' },
      { id: 'local-1', role: 'user', content: 'same', extra: { clientMessageId: 'client-1' } },
      { id: 'canonical-1', role: 'assistant', extra: { clientMessageId: 'client-1' } },
    ] as ChatMessage[];
    const { result } = renderHook(() => {
      const [messages, setMessages] = useState(initial);
      return { messages, cancel: useCancelledGroupChatMessages(setMessages) };
    });
    act(() => result.current.cancel(['client-1'], ['canonical-1']));
    expect(result.current.messages).toEqual([initial[1], initial[3]]);
  });
});
