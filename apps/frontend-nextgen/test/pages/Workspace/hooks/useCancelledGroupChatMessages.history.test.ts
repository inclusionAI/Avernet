/** @jest-environment jsdom */
import type { DeliveryStatusView } from '@/domain/collaboration/types';
import { useCancelledGroupChatMessages } from '@/pages/Workspace/hooks/useCancelledGroupChatMessages';
import { useMessageDeliveries } from '@/pages/Workspace/hooks/useMessageDeliveries';
import { cancelDelivery, querySessionDeliveries } from '@/services/backendApi/collaboration/deliveryController';
import { listSessionMessages, type SessionMessageData } from '@/services/backendApi/collaboration/sessionController';
import { GroupChatHistoryPaginator } from '@/services/workspace/groupChatHistoryPaginator';
import type { ChatMessage } from '@tc-chat/core';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { useState } from 'react';

// Keep paginator, mapper, delivery service and removal hooks real; replace only HTTP boundaries.
jest.mock('@/services/backendApi/collaboration/sessionController');
jest.mock('@/services/backendApi/collaboration/deliveryController');
const cancel = jest.mocked(cancelDelivery);

const history: SessionMessageData[] = [
  { id: 'reply', timestamp: 3000, sender: 'bot-a', content: 'same', message_type: 'bot', role: 'assistant' },
  { id: 'canonical-2', timestamp: 2000, sender: 'human-1', content: 'same', message_type: 'human', role: 'user' },
  { id: 'canonical-1', timestamp: 1000, sender: 'human-1', content: 'same', message_type: 'human', role: 'user' },
];
const delivery: DeliveryStatusView = {
  delivery_id: 'delivery-a', message_id: 'canonical-1', target_bot_id: 'bot-a',
  flow_kind: 'group', kind: 'send', status: 'cancelled', state_version: 2,
  run_id: null, wait_reason: null, admission_error: null,
};

beforeEach(() => {
  jest.resetAllMocks();
  jest.mocked(listSessionMessages).mockResolvedValue({ code: 20000, message: '', request_id: 'history', data: history });
  jest.mocked(querySessionDeliveries).mockResolvedValue([]);
});

function useHydratedChat(initial: ChatMessage[]) {
  const [messages, setMessages] = useState(initial);
  const remove = useCancelledGroupChatMessages(setMessages);
  const deliveries = useMessageDeliveries(null, 'session-1', remove);
  return { messages, cancel: deliveries.cancelDelivery };
}

describe('queued cancellation after history hydration', () => {
  it('removes only the exact canonical user message after the remaining target is cancelled', async () => {
    const messages = await new GroupChatHistoryPaginator('session-1', 'human-1').loadLatest();
    expect(messages.find((message) => message.id === 'canonical-1')?.extra?.clientMessageId).toBeUndefined();
    const { result } = renderHook(() => useHydratedChat(messages));
    // Cancelling one target alone does not authorize removal while another is queued.
    cancel.mockResolvedValueOnce([{ delivery, error: null }]);
    await act(async () => { await result.current.cancel('canonical-1', 'delivery-a'); });
    expect(result.current.messages.map((message) => message.id)).toEqual(['canonical-1', 'canonical-2', 'reply']);
    cancel.mockResolvedValueOnce([{
      delivery: { ...delivery, delivery_id: 'delivery-b', target_bot_id: 'bot-b' },
      error: null, client_message_id: 'optimistic-client-1',
    }]);
    await act(async () => { await result.current.cancel('canonical-1', 'delivery-b'); });
    expect(cancel).toHaveBeenLastCalledWith('canonical-1', 'delivery-b', 'session-1');
    expect(result.current.messages.map((message) => message.id)).toEqual(['canonical-2', 'reply']);
  });

  it('does not remove hydrated history on an errored result even if it carries IDs', async () => {
    const messages = await new GroupChatHistoryPaginator('session-1', 'human-1').loadLatest();
    const { result } = renderHook(() => useHydratedChat(messages));
    cancel.mockResolvedValue([{ delivery, error: 'delivery_state_changed', client_message_id: 'optimistic-client-1' }]);
    await act(async () => { await result.current.cancel('canonical-1', 'delivery-a'); });
    expect(result.current.messages).toEqual(messages);
  });

  it('does not infer removability from canonical ID or target status on an older server', async () => {
    const messages = await new GroupChatHistoryPaginator('session-1', 'human-1').loadLatest();
    const { result } = renderHook(() => useHydratedChat(messages));
    cancel.mockResolvedValue([{ delivery, error: null }]);
    await act(async () => { await result.current.cancel('canonical-1', 'delivery-a'); });
    expect(result.current.messages).toEqual(messages);
  });
});
