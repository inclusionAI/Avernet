import { isStreamingAssistantMessage } from '@/pages/Workspace/components/GroupChatPane/messageHelpers';
import { describe, expect, it } from '@jest/globals';
import type { ChatMessage } from '@tc-chat/core';

function assistantMessage(status: ChatMessage['status']): ChatMessage {
  return { id: 'm1', role: 'assistant', content: '', status } as ChatMessage;
}

describe('isStreamingAssistantMessage', () => {
  it('streaming 态 assistant 按流式渲染（不依赖兜底）', () => {
    expect(isStreamingAssistantMessage(assistantMessage('streaming'), false, false)).toBe(true);
    expect(isStreamingAssistantMessage(assistantMessage('streaming'), true, true)).toBe(true);
  });

  it('兜底仅覆盖 pending 占位的时序间隙：最后一条 + 请求中', () => {
    expect(isStreamingAssistantMessage(assistantMessage('pending'), true, true)).toBe(true);
  });

  it('pending 但非最后一条 / 非请求中不兜底', () => {
    expect(isStreamingAssistantMessage(assistantMessage('pending'), false, true)).toBe(false);
    expect(isStreamingAssistantMessage(assistantMessage('pending'), true, false)).toBe(false);
  });

  it('终态消息不得按流式渲染：aborted（终止后空气泡不再显示省略号）', () => {
    // 回归场景：abort 后 SDK 在 allDone=false 时不发 onComplete，isRequesting 滞留为 true，
    // 若兜底不排除终态，已终止的空气泡会一直渲染 LoadingDots。
    expect(isStreamingAssistantMessage(assistantMessage('aborted'), true, true)).toBe(false);
  });

  it('终态消息不得按流式渲染：done / error / history', () => {
    expect(isStreamingAssistantMessage(assistantMessage('done'), true, true)).toBe(false);
    expect(isStreamingAssistantMessage(assistantMessage('error'), true, true)).toBe(false);
    expect(isStreamingAssistantMessage(assistantMessage('history'), true, true)).toBe(false);
  });

  it('非 assistant 角色永不按流式渲染', () => {
    const userMessage = { id: 'u1', role: 'user', content: 'hi', status: 'done' } as ChatMessage;
    expect(isStreamingAssistantMessage(userMessage, true, true)).toBe(false);
  });
});
