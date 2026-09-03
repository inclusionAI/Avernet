/** @jest-environment jsdom */
import {
  buildExplainPrompt,
  getLatestUserMessageId,
  useMessageInteractions,
} from '@/pages/Workspace/hooks/useMessageInteractions';
import { describe, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

const messages = [
  { id: 'm1', role: 'assistant', content: '第一条', status: 'history' },
  { id: 'm2', role: 'user', content: '第二条', status: 'history' },
] as never[];

describe('useMessageInteractions', () => {
  it('copies text and exposes a quote draft without changing message contracts', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const { result } = renderHook(() => useMessageInteractions({ sessionId: 's1', messages, isRequesting: false }));

    let copied = false;
    await act(async () => {
      copied = await result.current.copyText('消息正文', '消息');
    });
    expect(copied).toBe(true);
    expect(writeText).toHaveBeenCalledWith('消息正文');
    expect(result.current.quote).toBeNull();

    act(() => result.current.quoteMessage('m1', 'Bot 甲', '引用片段'));
    expect(result.current.quote).toEqual({ messageId: 'm1', senderName: 'Bot 甲', text: '引用片段' });
    act(() => result.current.clearQuote());
    expect(result.current.quote).toBeNull();
  });

  it('resets selection, quote, and unread count when switching sessions', () => {
    const { result, rerender } = renderHook(
      ({ sessionId }) => useMessageInteractions({ sessionId, messages, isRequesting: false }),
      { initialProps: { sessionId: 's1' } },
    );
    act(() => {
      result.current.quoteMessage('m1', 'Bot 甲', '引用片段');
      result.current.setSelection({ messageId: 'm1', text: '片段', rect: new DOMRect(0, 0, 10, 10) });
    });
    rerender({ sessionId: 's2' });
    expect(result.current.quote).toBeNull();
    expect(result.current.selection).toBeNull();
    expect(result.current.unreadCount).toBe(0);
  });

  it('counts newly appended messages only while away from the bottom', () => {
    const { result, rerender } = renderHook(
      ({ currentMessages }) =>
        useMessageInteractions({ sessionId: 's1', messages: currentMessages, isRequesting: false }),
      { initialProps: { currentMessages: messages } },
    );
    act(() => result.current.setAtBottom(false));
    rerender({
      currentMessages: [...messages, { id: 'm3', role: 'assistant', content: '第三条', status: 'history' }] as never[],
    });
    expect(result.current.unreadCount).toBe(1);
    rerender({
      currentMessages: [
        ...messages,
        { id: 'm3', role: 'assistant', content: '第三条更新', status: 'streaming' },
      ] as never[],
    });
    expect(result.current.unreadCount).toBe(1);
    act(() => result.current.markRead());
    expect(result.current.unreadCount).toBe(0);
  });

  it('stops generation with Escape when a request is active', () => {
    const onStop = jest.fn();
    renderHook(() => useMessageInteractions({ sessionId: 's1', messages, isRequesting: true, onStop }));
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});

describe('buildExplainPrompt', () => {
  it('builds an editable explanation prompt with the selected text as quoted context', () => {
    expect(buildExplainPrompt('Bot 甲', '第一行\n第二行')).toBe('请解释以下内容（来自 Bot 甲）：\n> 第一行\n> 第二行');
  });
});

describe('getLatestUserMessageId', () => {
  it('finds the latest user message even when the final message is from a Bot or system', () => {
    expect(
      getLatestUserMessageId([
        { id: 'u1', role: 'user', content: '旧问题', status: 'history' },
        { id: 'a1', role: 'assistant', content: '回答', status: 'history' },
        { id: 'u2', role: 'user', content: '新问题', status: 'history' },
        { id: 's1', role: 'system', content: '系统提示', status: 'history' },
      ] as never[]),
    ).toBe('u2');
    expect(getLatestUserMessageId([])).toBeNull();
  });
});
