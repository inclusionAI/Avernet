/** @jest-environment jsdom */
import { ConversationSessionRow } from '@/pages/Workspace/Chat/components/ConversationSessionRow';
import { SessionCard } from '@/pages/Workspace/components/SessionCard';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

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
    const button = screen.getByRole('button', { name: '对话会话 21 条' });
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

it('renders a separate favorite action that does not select the row', () => {
  const onSelect = jest.fn();
  const onToggleFavorite = jest.fn();
  render(
    <ConversationSessionRow
      session={{ ...session, favorite: false }}
      selected={false}
      onSelect={onSelect}
      onToggleFavorite={onToggleFavorite}
    />,
  );
  const star = screen.getByRole('button', { name: '收藏会话' });
  expect(star).toHaveClass('opacity-0', 'group-hover/row:opacity-100', 'group-focus-within/row:opacity-100');
  expect(star.parentElement?.closest('button')).toBeNull();
  fireEvent.click(star);
  expect(onToggleFavorite).toHaveBeenCalledTimes(1);
  expect(onSelect).not.toHaveBeenCalled();
});
it('keeps a filled favorite star visible and disables it while pending', () => {
  render(
    <ConversationSessionRow
      session={{ ...session, favorite: true }}
      selected
      onSelect={jest.fn()}
      onToggleFavorite={jest.fn()}
      favoritePending
    />,
  );
  const star = screen.getByRole('button', { name: '取消收藏' });
  expect(star).toBeDisabled();
  expect(star).not.toHaveClass('opacity-0');
  expect(star.querySelector('svg')).toHaveClass('fill-warning', 'text-warning');
});
it('does not render favorite actions for read-only sessions even if a callback is provided', () => {
  render(
    <ConversationSessionRow
      session={session}
      selected={false}
      readOnly
      onSelect={jest.fn()}
      onToggleFavorite={jest.fn()}
    />,
  );
  expect(screen.queryByRole('button', { name: '收藏会话' })).not.toBeInTheDocument();
});
it('disables unknown favorite states rather than guessing an inverse action', () => {
  render(
    <ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} onToggleFavorite={jest.fn()} />,
  );
  expect(screen.getByRole('button', { name: '收藏会话' })).toBeDisabled();
});

it('places the favorite action before the message count and keeps count clicks selecting the session', () => {
  const onSelect = jest.fn();
  render(
    <ConversationSessionRow
      session={{ ...session, favorite: true }}
      selected={false}
      onSelect={onSelect}
      onToggleFavorite={jest.fn()}
    />,
  );
  const star = screen.getByRole('button', { name: '取消收藏' });
  const count = screen.getByText('21 条');
  expect(star.compareDocumentPosition(count) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(count);
  expect(onSelect).toHaveBeenCalledTimes(1);
});

it.each([0, 9, 10, 99, 100, 9999, 123456789])(
  'reserves the same count column for %s messages so favorite stars stay aligned',
  (messageCount) => {
    render(
      <ConversationSessionRow
        session={{ ...session, messageCount, favorite: true }}
        selected={false}
        onSelect={jest.fn()}
        onToggleFavorite={jest.fn()}
      />,
    );
    const star = screen.getByRole('button', { name: '取消收藏' });
    const slot = star.nextElementSibling;
    expect(slot).toHaveClass('w-14', 'shrink-0', 'justify-end');
    expect(slot?.textContent).toBe(messageCount > 0 ? `${messageCount} 条` : '');
  },
);

it('constrains long counts without adding another keyboard tab stop', () => {
  render(
    <ConversationSessionRow
      session={{ ...session, messageCount: 123456789, favorite: true }}
      selected={false}
      onSelect={jest.fn()}
      onToggleFavorite={jest.fn()}
    />,
  );
  const count = screen.getByRole('button', { name: '123456789 条' });
  expect(count).toHaveAttribute('tabindex', '-1');
  expect(count).toHaveClass('w-full', 'min-w-0', 'justify-end');
  expect(screen.getByText('123456789 条')).toHaveClass('truncate');
});
