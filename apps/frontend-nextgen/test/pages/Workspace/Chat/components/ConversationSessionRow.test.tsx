/** @jest-environment jsdom */
import { ConversationSessionRow } from '@/pages/Workspace/Chat/components/ConversationSessionRow';
import { formatSessionTime, formatSessionTimeTooltip, SessionCard } from '@/pages/Workspace/components/SessionCard';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const session = {
  sessionId: 'session-1',
  botId: 'bot-1',
  title: '对话会话',
  messageCount: 21,
  gmtCreate: '',
  gmtModified: '',
};

describe('ConversationSessionRow visual parity', () => {
  it.each([false, true])('matches group session typography and tree lines (selected=%s)', (selected) => {
    const { container } = render(
      <>
        <ConversationSessionRow session={session} selected={selected} onSelect={jest.fn()} />
        <SessionCard
          title="协作群会话"
          subtitle=""
          compact
          indicator="message"
          selected={selected}
          onSelect={jest.fn()}
        />
      </>,
    );
    const title = screen.getByText('对话会话');
    const reference = screen.getByText('协作群会话');
    for (const token of [
      'text-xs',
      'leading-5',
      selected ? 'font-medium' : 'font-normal',
      selected ? 'text-primary' : 'text-foreground',
    ]) {
      expect(reference).toHaveClass(token);
      expect(title).toHaveClass(token);
    }
    const [row, groupRow] = Array.from(container.children);
    for (const selector of ['[data-session-tree-rail]', '[data-session-tree-elbow]']) {
      const referenceLine = groupRow.querySelector(selector)!;
      const line = row.querySelector(selector);
      expect(line).toHaveAttribute('aria-hidden', 'true');
      expect(line).toHaveClass(...Array.from(referenceLine.classList));
    }
    expect(row).toHaveClass('last:[&_[data-session-tree-rail]]:bottom-1/2');
  });

  it('keeps message counts and session selection', () => {
    const onSelect = jest.fn();
    render(<ConversationSessionRow session={session} selected onSelect={onSelect} />);
    const button = screen.getByRole('button', { name: '对话会话' });
    expect(screen.getByText('21 条')).toBeInTheDocument();
    expect(button).toHaveAttribute('aria-current', 'page');
    fireEvent.click(button);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('keeps the read-only badge instead of the message count', () => {
    render(<ConversationSessionRow session={session} selected={false} readOnly onSelect={jest.fn()} />);
    expect(screen.getByRole('button', { name: '对话会话 只读' })).toBeInTheDocument();
    expect(screen.queryByText('21 条')).not.toBeInTheDocument();
  });
});

const actions = { run: jest.fn(async () => true), pending: false };

it('moves favorite into the first menu item, without selecting the session', async () => {
  const onSelect = jest.fn(),
    onToggleFavorite = jest.fn();
  render(
    <ConversationSessionRow
      session={{ ...session, favorite: false }}
      selected={false}
      onSelect={onSelect}
      onToggleFavorite={onToggleFavorite}
      actions={actions}
    />,
  );
  expect(screen.queryByRole('button', { name: '收藏会话' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
  const menu = await screen.findByRole('dialog');
  const items = within(menu).getAllByRole('button');
  expect(items.map((item) => item.textContent)).toEqual(['收藏会话', '编辑标题', '清除上下文', '删除会话']);
  await userEvent.click(items[0]);
  expect(onToggleFavorite).toHaveBeenCalledTimes(1);
  expect(onSelect).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('shows cancel-favorite and filled star only inside the menu', async () => {
  render(
    <ConversationSessionRow
      session={{ ...session, favorite: true }}
      selected
      onSelect={jest.fn()}
      onToggleFavorite={jest.fn()}
      actions={actions}
    />,
  );
  expect(screen.queryByRole('button', { name: '取消收藏' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
  expect((await screen.findByRole('button', { name: '取消收藏' })).querySelector('svg')).toHaveClass(
    'fill-warning',
    'text-warning',
  );
});

it('unknown favorite state is disabled in the menu instead of guessing its inverse', async () => {
  render(
    <ConversationSessionRow
      session={session}
      selected={false}
      onSelect={jest.fn()}
      onToggleFavorite={jest.fn()}
      actions={actions}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
  expect(await screen.findByRole('button', { name: '收藏会话' })).toBeDisabled();
});

it('read-only rows show creation time but no writable menu even with callbacks', () => {
  const gmtCreate = '2025-01-02 12:30:00';
  render(
    <ConversationSessionRow
      session={{ ...session, gmtCreate }}
      selected={false}
      readOnly
      onSelect={jest.fn()}
      onToggleFavorite={jest.fn()}
      actions={actions}
    />,
  );
  expect(screen.queryByRole('button', { name: '会话更多操作' })).not.toBeInTheDocument();
  const date = screen.getByText(formatSessionTime(gmtCreate));
  expect(date.className).not.toContain('group-hover/row:opacity-0');
});

it.each([false, true])(
  'creation time follows count and is replaced only on hover/focus, not selection=%s',
  (selected) => {
    const gmtCreate = '2025-01-02 12:30:00';
    render(
      <ConversationSessionRow
        session={{ ...session, gmtCreate, gmtModified: '2024-01-01 01:01:00' }}
        selected={selected}
        onSelect={jest.fn()}
        actions={actions}
      />,
    );
    const date = screen.getByText(formatSessionTime(gmtCreate));
    expect(date).toHaveClass(
      'min-w-9',
      '[@media(hover:hover)]:group-hover/row:opacity-0',
      '[@media(hover:hover)]:group-focus-within/row:opacity-0',
    );
    expect(date).not.toHaveClass('opacity-0');
    expect(screen.getByText('21 条').compareDocumentPosition(date) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const slot = screen.getByRole('button', { name: '会话更多操作' }).closest('[data-session-meta-actions]');
    expect(slot).toHaveClass(
      'absolute',
      'right-0',
      'opacity-0',
      'group-hover/row:opacity-100',
      'group-focus-within/row:opacity-100',
      '[@media(hover:none)]:relative',
      '[@media(hover:none)]:opacity-100',
    );
  },
);

it('uses the same complete-date tooltip as group sessions', async () => {
  const gmtCreate = '2025-01-02 12:30:00';
  render(<ConversationSessionRow session={{ ...session, gmtCreate }} selected={false} onSelect={jest.fn()} />);
  await userEvent.hover(screen.getByText(formatSessionTime(gmtCreate)));
  expect(await screen.findByRole('tooltip')).toHaveTextContent(formatSessionTimeTooltip(gmtCreate));
});

it.each(['', 'not-a-date'])('does not invent a creation date for %s, and keeps menu reachable', (gmtCreate) => {
  render(
    <ConversationSessionRow
      session={{ ...session, gmtCreate, gmtModified: '2025-01-02 12:30:00' }}
      selected={false}
      onSelect={jest.fn()}
      actions={actions}
    />,
  );
  expect(screen.queryByText('Invalid Date')).not.toBeInTheDocument();
  expect(screen.queryByText('2025/01/02')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '会话更多操作' })).toBeInTheDocument();
});

it.each([0, 9, 10, 99, 100, 9999, 123456789])(
  'keeps a fixed count column for %s messages before the date/action slot',
  (messageCount) => {
    const { container } = render(
      <ConversationSessionRow
        session={{ ...session, messageCount, gmtCreate: '2025-01-02 12:30:00' }}
        selected={false}
        onSelect={jest.fn()}
        actions={actions}
      />,
    );
    const slot = container.querySelector('[data-session-message-count]');
    expect(slot).toHaveClass('w-14', 'shrink-0', 'justify-end');
    expect(slot?.textContent).toBe(messageCount > 0 ? `${messageCount} 条` : '');
  },
);

it('count clicks select the session while long counts truncate without adding a tab stop', () => {
  const onSelect = jest.fn();
  render(
    <ConversationSessionRow
      session={{ ...session, messageCount: 123456789 }}
      selected={false}
      onSelect={onSelect}
      actions={actions}
    />,
  );
  const count = screen.getByRole('button', { name: '123456789 条' });
  expect(count).toHaveAttribute('tabindex', '-1');
  expect(count).toHaveClass('w-full', 'min-w-0', 'justify-end');
  expect(screen.getByText('123456789 条')).toHaveClass('truncate');
  fireEvent.click(count);
  expect(onSelect).toHaveBeenCalledTimes(1);
});

it.each([
  ['2026-10-09 09:15:00', '09:15'],
  ['2026-10-08 09:15:00', '昨天'],
  ['2026-10-06 09:15:00', '周二'],
  ['2026-09-01 09:15:00', '09/01'],
  ['2025-12-01 09:15:00', '2025/12/01'],
])('uses the group session format for creation time %s → %s', (gmtCreate, expected) => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date('2026-10-09T12:00:00'));
  try {
    render(
      <ConversationSessionRow
        session={{ ...session, gmtCreate, gmtModified: '2026-10-09 12:00:00' }}
        selected
        onSelect={jest.fn()}
        actions={actions}
      />,
    );
    expect(screen.getByText(expected)).toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});
it('keeps more visible and date hidden while the menu is open even after leaving the row', async () => {
  render(
    <ConversationSessionRow
      session={{ ...session, gmtCreate: '2025-01-02 12:30:00' }}
      selected={false}
      onSelect={jest.fn()}
      actions={actions}
    />,
  );
  const more = screen.getByRole('button', { name: '会话更多操作' });
  await userEvent.click(more);
  expect(more.closest('[data-session-meta-actions]')).toHaveClass('opacity-100');
  expect(screen.getByText('2025/01/02')).toHaveClass('[@media(hover:hover)]:opacity-0');
});
it('blocks the menu during an ongoing favorite request and re-enables it when done', () => {
  const props = {
    session: { ...session, favorite: true },
    selected: false,
    onSelect: jest.fn(),
    onToggleFavorite: jest.fn(),
    actions,
  };
  const { rerender } = render(<ConversationSessionRow {...props} favoritePending />);
  expect(screen.getByRole('button', { name: '会话更多操作' })).toBeDisabled();
  rerender(<ConversationSessionRow {...props} favoritePending={false} />);
  expect(screen.getByRole('button', { name: '会话更多操作' })).toBeEnabled();
});
