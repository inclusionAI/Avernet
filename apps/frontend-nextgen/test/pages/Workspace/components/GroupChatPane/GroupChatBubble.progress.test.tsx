/** @jest-environment jsdom */
import { GroupChatBubble } from '@/pages/Workspace/components/GroupChatPane/GroupChatBubble';
import { describe, expect, it, jest } from '@jest/globals';
import type { ChatMessage } from '@tc-chat/core';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

jest.mock('@tc-chat/ui/es/Bubble', () => ({ Bubble: () => <div>消息正文</div> }));
jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({ aixUiPlugin: {}, fileRefPlugin: {} }));

const props = { group: {} as never, participants: [] as never, isLastMessage: false, isRequesting: true };

describe('GroupChatBubble progress', () => {
  it('marks every streaming assistant reply independently of its position', () => {
    render(
      <>
        <GroupChatBubble
          {...props}
          message={{ id: 'a', role: 'assistant', content: 'A', status: 'streaming', extra: { botUuid: 'bot-a' } }}
        />
        <GroupChatBubble
          {...props}
          isLastMessage
          message={{ id: 'b', role: 'assistant', content: 'B', status: 'streaming', extra: { botUuid: 'bot-b' } }}
        />
      </>,
    );
    expect(screen.getAllByRole('img', { name: '正在回复' })).toHaveLength(2);
    expect(screen.queryByText('输出中')).not.toBeInTheDocument();
  });

  it.each(['done', 'aborted', 'error'] as ChatMessage['status'][])(
    'does not mark a terminal %s reply as active',
    (status) => {
      render(
        <GroupChatBubble
          {...props}
          isLastMessage
          message={{ id: 'a', role: 'assistant', content: 'A', status, extra: { botUuid: 'bot-a' } }}
        />,
      );
      expect(screen.queryByRole('img', { name: '正在回复' })).not.toBeInTheDocument();
    },
  );
});
