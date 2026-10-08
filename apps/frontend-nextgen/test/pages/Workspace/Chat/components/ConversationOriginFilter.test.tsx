/** @jest-environment jsdom */
import { ConversationOriginFilter } from '@/pages/Workspace/Chat/components/ConversationOriginFilter';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const callbacks = () => ({
  onOriginChange: jest.fn(),
  onScopeChange: jest.fn(),
});

describe('ConversationOriginFilter', () => {
  it('shows scope only for managed mine and hides it for others/friend Bot', async () => {
    const { onOriginChange, onScopeChange } = callbacks();
    render(
      <ConversationOriginFilter
        botId="bot-a:2088"
        origin="mine"
        scope="all"
        onOriginChange={onOriginChange}
        onScopeChange={onScopeChange}
      />,
    );
    expect(screen.getByText('发起归属')).toBeInTheDocument();
    expect(screen.getByText('会话范围')).toBeInTheDocument();
    expect(screen.getByText('仅看已收藏')).toBeInTheDocument();
  });

  it('hides the scope group for others origin', () => {
    const { onOriginChange, onScopeChange } = callbacks();
    render(
      <ConversationOriginFilter
        botId="bot-a:2088"
        origin="others"
        scope="all"
        onOriginChange={onOriginChange}
        onScopeChange={onScopeChange}
      />,
    );
    expect(screen.getByText('发起归属')).toBeInTheDocument();
    expect(screen.queryByText('会话范围')).not.toBeInTheDocument();
    expect(screen.queryByText('仅看已收藏')).not.toBeInTheDocument();
  });

  it('marks the active origin and scope options', () => {
    const { onOriginChange, onScopeChange } = callbacks();
    render(
      <ConversationOriginFilter
        botId="bot-a:2088"
        origin="mine"
        scope="favorite"
        onOriginChange={onOriginChange}
        onScopeChange={onScopeChange}
      />,
    );
    expect(screen.getByRole('radio', { name: '我发起的' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: '他人发起的' })).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByRole('radio', { name: '全部会话' })).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByRole('radio', { name: '仅看已收藏' })).toHaveAttribute('aria-checked', 'true');
  });

  it('emits origin change and closes the popover', () => {
    const { onOriginChange, onScopeChange } = callbacks();
    render(
      <ConversationOriginFilter
        botId="bot-a:2088"
        origin="mine"
        scope="all"
        onOriginChange={onOriginChange}
        onScopeChange={onScopeChange}
      />,
    );
    const trigger = screen.getByRole('button', { name: '发起归属与会话范围' });
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('radio', { name: '他人发起的' }));
    expect(onOriginChange).toHaveBeenCalledWith('others');
    expect(onScopeChange).not.toHaveBeenCalled();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('emits scope change only within the scope group', () => {
    const { onOriginChange, onScopeChange } = callbacks();
    render(
      <ConversationOriginFilter
        botId="bot-a:2088"
        origin="mine"
        scope="all"
        onOriginChange={onOriginChange}
        onScopeChange={onScopeChange}
      />,
    );
    fireEvent.click(screen.getByRole('radio', { name: '仅看已收藏' }));
    expect(onScopeChange).toHaveBeenCalledWith('favorite');
    expect(onOriginChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('radio', { name: '全部会话' }));
    expect(onScopeChange).toHaveBeenCalledWith('all');
  });
});

it('hides every scope option and its indicator when favorites are unsupported', () => {
  render(
    <ConversationOriginFilter
      botId="teclaw"
      origin="mine"
      scope="favorite"
      supportsFavorites={false}
      {...callbacks()}
    />,
  );
  expect(screen.getByText('我发起的')).toBeInTheDocument();
  expect(screen.getByText('他人发起的')).toBeInTheDocument();
  expect(screen.queryByText('会话范围')).not.toBeInTheDocument();
  expect(screen.queryByText('全部会话')).not.toBeInTheDocument();
  expect(screen.queryByText('仅看已收藏')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '发起归属' })).not.toHaveClass('bg-primary/10');
});
