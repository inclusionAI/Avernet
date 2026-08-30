/** @jest-environment jsdom */
import { ResourceManageModal } from '@/pages/Workspace/components/Modals/ResourceManageModal';
import { expect, it, jest } from '@jest/globals';
import type { ChatMessage } from '@tc-chat/core';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

it('renders attachment name as link when present', () => {
  const messages: ChatMessage[] = [
    {
      id: 'm1',
      role: 'user',
      content: '',
      status: 'history',
      blocks: [
        // 兼容形态 1：约定 attachment 块（带 name/url）
        { type: 'attachment', name: '需求文档.pdf', url: 'https://example.com/r.pdf' } as never,
      ],
    },
  ];
  render(<ResourceManageModal sessionId="s1" messages={messages} onClose={jest.fn()} />);
  const link = screen.getByRole('link', { name: '需求文档.pdf' });
  expect(link).toBeInTheDocument();
  expect(link).toHaveAttribute('href', 'https://example.com/r.pdf');
});

it('shows empty hint when no attachment blocks', () => {
  const messages: ChatMessage[] = [
    {
      id: 'm1',
      role: 'user',
      content: 'hi',
      status: 'history',
      blocks: [{ type: 'text', content: 'hi' } as never],
    },
  ];
  render(<ResourceManageModal sessionId="s1" messages={messages} onClose={jest.fn()} />);
  expect(screen.getByText('当前会话暂无附件')).toBeInTheDocument();
});

it('onClose button fires onClose callback', () => {
  const onClose = jest.fn();
  render(<ResourceManageModal sessionId="s1" messages={[]} onClose={onClose} />);
  fireEvent.click(screen.getByRole('button', { name: /关闭/ }));
  expect(onClose).toHaveBeenCalledTimes(1);
});
