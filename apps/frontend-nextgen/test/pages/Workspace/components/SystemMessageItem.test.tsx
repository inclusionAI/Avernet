/** @jest-environment jsdom */
import { SystemMessageItem } from '@/pages/Workspace/components/GroupChatPane/SystemMessageItem';
import type { ChatMessage } from '@tc-chat/core';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

it.each(['用户 风太 已加入会话', '运行通知：这是一段较长的系统事件详情\n附加信息保留原文'])(
  '鼠标移入立即显示完整系统消息：%s',
  (content) => {
    const message = { id: 'system-1', role: 'system', content, createdAt: 0 } as ChatMessage;
    const { container } = render(<SystemMessageItem message={message} />);
    const trigger = container.querySelector('p')!;
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    fireEvent.pointerMove(trigger, { pointerType: 'mouse' });
    // 仅执行零毫秒计时器：不能依赖浏览器 title 或 Tooltip 默认的数百毫秒等待。
    act(() => jest.advanceTimersByTime(0));
    expect(screen.getByRole('tooltip')).toHaveTextContent(content.replace(/\s+/g, ' '));
    expect(trigger).not.toHaveAttribute('title');
    fireEvent.keyDown(trigger, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  },
);
