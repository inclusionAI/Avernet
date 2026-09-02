/** @jest-environment jsdom */
import { MessageSenderLayout, MessageSenderMeta } from '@/components/Workspace/MessageSenderMeta';
import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const avatar = <span data-testid="sender-avatar">风</span>;

describe('MessageSenderLayout', () => {
  it('keeps avatar and sender metadata in the same top-aligned row for assistant messages', () => {
    render(
      <MessageSenderLayout
        avatar={avatar}
        align="left"
        meta={<MessageSenderMeta name="风大OC" time="08-25 20:07" align="left" />}
      >
        <div data-testid="message-content">消息正文</div>
      </MessageSenderLayout>,
    );

    const layout = screen.getByTestId('sender-avatar').parentElement?.parentElement;
    expect(layout).toHaveClass('flex', 'items-start', 'gap-3');
    expect(screen.getByTestId('message-sender-meta')).toHaveTextContent('风大OC');
    expect(screen.getByTestId('message-sender-meta')).toHaveTextContent('08-25 20:07');
  });

  it('keeps right-aligned user metadata next to the avatar without the legacy 44px inset', () => {
    render(
      <MessageSenderLayout
        avatar={avatar}
        align="right"
        meta={<MessageSenderMeta name="章梧" time="10:30" align="right" />}
      >
        <div data-testid="message-content">用户消息</div>
      </MessageSenderLayout>,
    );

    const meta = screen.getByTestId('message-sender-meta');
    expect(meta).toHaveClass('justify-end', 'text-right');
    expect(meta).not.toHaveClass('pr-11');
  });
});
