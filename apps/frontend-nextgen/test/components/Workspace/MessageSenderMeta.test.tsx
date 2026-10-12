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

  it('SVG 稿实测：元信息行 13px 常规字重，13px 名称/时间空隙并排（无分隔点），与内容首行留呼吸间距（mb-1.5）', () => {
    render(
      <MessageSenderLayout
        avatar={avatar}
        align="left"
        meta={<MessageSenderMeta name="风太OC" time="09-14 10:00" align="left" />}
      >
        <div data-testid="message-content">消息内容</div>
      </MessageSenderLayout>,
    );
    const meta = screen.getByTestId('message-sender-meta');
    expect(meta).toHaveClass('mt-1', 'mb-1.5', 'text-[13px]');
    // SVG 稿：名称与时间并排无「·」分隔点。
    expect(meta).toHaveTextContent(/风太OC/);
    expect(meta).toHaveTextContent(/09-14 10:00/);
    expect(meta.textContent).not.toContain('·');
  });

  it('dmore 实测：Bot 侧名称 400/#525252 在前、时间 12px #71717A 在后', () => {
    render(<MessageSenderMeta name="本地代码专家" time="15:48" align="left" />);
    const meta = screen.getByTestId('message-sender-meta');
    const [name, time] = Array.from(meta.children);
    expect(name).toHaveClass('font-normal', 'text-content-strong');
    expect(time).toHaveClass('text-xs', 'text-muted-foreground');
    // DOM 顺序 = 名称在前、时间在后。
    expect(name.compareDocumentPosition(time) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('用户侧时间在前、名称在后，名称不加重颜色（与 Bot 侧同用 content-strong）', () => {
    render(<MessageSenderMeta name="陈燕平" time="15:20" align="right" />);
    const meta = screen.getByTestId('message-sender-meta');
    const [time, name] = Array.from(meta.children);
    expect(time).toHaveClass('text-xs', 'text-muted-foreground');
    expect(name).toHaveClass('font-normal', 'text-content-strong');
    expect(name).not.toHaveClass('font-medium', 'text-foreground');
    expect(time.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('dmore 实测：头像缺省(null)时 Bot 消息裸排版，不渲染头像槽位', () => {
    const { container } = render(
      <MessageSenderLayout avatar={null} align="left" meta={<MessageSenderMeta name="Bot" align="left" />}>
        <div>内容</div>
      </MessageSenderLayout>,
    );
    expect(container.querySelector('[data-testid="sender-avatar"]')).toBeNull();
    expect(screen.getByTestId('message-sender-meta')).toBeInTheDocument();
  });
});
