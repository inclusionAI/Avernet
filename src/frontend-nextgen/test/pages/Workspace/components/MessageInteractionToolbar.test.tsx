/** @jest-environment jsdom */
import {
  MessageCopyAction,
  MessageEditBar,
  MessageInteractionToolbar,
  MessageQuoteBar,
  MessageSelectionToolbar,
} from '@/components/Workspace/MessageInteractionToolbar';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/components/ui/Tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

describe('MessageInteractionToolbar', () => {
  it('renders accessible copy, quote, and edit actions and responds to keyboard activation', () => {
    const onCopy = jest.fn();
    const onEdit = jest.fn();
    render(<MessageInteractionToolbar onCopy={onCopy} onEdit={onEdit} isEditable />);

    const copy = screen.getByRole('button', { name: '复制消息' });
    const edit = screen.getByRole('button', { name: '编辑消息' });
    expect(copy).toHaveClass('h-7', 'w-7');
    expect(screen.queryByRole('button', { name: '引用消息' })).not.toBeInTheDocument();

    fireEvent.keyDown(copy, { key: 'Enter' });
    fireEvent.click(edit);
    expect(onCopy).toHaveBeenCalledTimes(1);
    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it('does not render edit action unless the message is editable', () => {
    render(<MessageInteractionToolbar onCopy={jest.fn()} onEdit={jest.fn()} />);
    expect(screen.queryByRole('button', { name: '编辑消息' })).not.toBeInTheDocument();
  });

  it('supports hiding the hover copy action when a permanent copy action is present', () => {
    render(<MessageInteractionToolbar onCopy={jest.fn()} showCopy={false} />);

    expect(screen.queryByRole('button', { name: '复制消息' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '引用消息' })).not.toBeInTheDocument();
  });

  it('renders permanently visible edit and copy actions at the message end', async () => {
    const onCopy = jest.fn().mockResolvedValue(true);
    const onEdit = jest.fn();
    render(<MessageCopyAction testId="message-copy-action" align="right" onCopy={onCopy} onEdit={onEdit} isEditable />);

    const action = screen.getByTestId('message-copy-action');
    expect(action).toHaveClass('relative', 'z-10', 'mt-0', 'justify-end', 'pr-11');
    expect(action).not.toHaveClass('min-h-8');
    expect(screen.getByRole('toolbar', { name: '消息底部操作' })).toHaveClass('pt-1', 'gap-0.5');
    const copy = screen.getByRole('button', { name: '复制整条消息' });
    const edit = screen.getByRole('button', { name: '编辑消息' });
    expect(copy).toHaveClass('h-7', 'w-7');
    expect(edit).toHaveClass('h-7', 'w-7');

    fireEvent.keyDown(copy, { key: 'Enter' });
    expect(await screen.findByTestId('message-copy-action-feedback')).toHaveTextContent('已复制');
    expect(await screen.findByRole('button', { name: '已复制' })).toBeInTheDocument();
    fireEvent.mouseEnter(edit);
    fireEvent.click(edit);
    expect(onCopy).toHaveBeenCalledTimes(1);
    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it('复制失败时显示明确的按钮反馈而不是误报成功', async () => {
    const onCopy = jest.fn().mockResolvedValue(false);
    render(<MessageCopyAction testId="message-copy-action" align="left" onCopy={onCopy} />);

    fireEvent.click(screen.getByRole('button', { name: '复制整条消息' }));
    expect(await screen.findByTestId('message-copy-action-feedback')).toHaveTextContent('复制失败');
    expect(await screen.findByRole('button', { name: '复制失败' })).toBeInTheDocument();
  });
});

describe('MessageSelectionToolbar', () => {
  it('shows selection actions and quotes without sending automatically', () => {
    const onCopy = jest.fn();
    const onQuote = jest.fn();
    const onExplain = jest.fn();
    render(
      <MessageSelectionToolbar
        selection={{
          messageId: 'm1',
          text: '选中的内容',
          rect: { left: 40, top: 100, width: 60, height: 20, right: 100, bottom: 120 } as DOMRect,
        }}
        onCopy={onCopy}
        onQuote={onQuote}
        onExplain={onExplain}
      />,
    );

    expect(screen.getByRole('toolbar', { name: '文本选择操作' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '复制选中文本' }));
    fireEvent.click(screen.getByRole('button', { name: '追问选中文本' }));
    fireEvent.click(screen.getByRole('button', { name: '解释选中文本' }));
    expect(onCopy).toHaveBeenCalledWith('选中的内容');
    expect(onQuote).toHaveBeenCalledWith('选中的内容');
    expect(onExplain).toHaveBeenCalledWith('选中的内容');
  });

  it('does not render when selection is empty', () => {
    render(<MessageSelectionToolbar selection={null} onCopy={jest.fn()} onQuote={jest.fn()} />);
    expect(screen.queryByRole('toolbar', { name: '文本选择操作' })).not.toBeInTheDocument();
  });
});

describe('MessageEditBar', () => {
  it('explains that editing will resend as a new message and supports cancellation', () => {
    const onCancel = jest.fn();
    render(<MessageEditBar onCancel={onCancel} />);
    expect(screen.getByRole('status', { name: '编辑消息状态' })).toHaveTextContent('将作为新消息发送');
    fireEvent.click(screen.getByRole('button', { name: '取消编辑' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe('MessageQuoteBar', () => {
  it('renders quote context and supports dismissal', () => {
    const onClear = jest.fn();
    render(
      <MessageQuoteBar quote={{ messageId: 'm1', senderName: 'Bot 甲', text: '第一行\n第二行' }} onClear={onClear} />,
    );
    const quoteBar = screen.getByText(/引用 Bot 甲/).parentElement?.parentElement;
    expect(quoteBar).toBeInTheDocument();
    expect(quoteBar).toHaveClass('relative', 'z-10', 'shrink-0', 'border', 'border-border', 'bg-muted');
    expect(quoteBar).not.toHaveClass('bg-muted/50', 'mb-2');
    expect(screen.getByText(/第一行/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消引用' }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
