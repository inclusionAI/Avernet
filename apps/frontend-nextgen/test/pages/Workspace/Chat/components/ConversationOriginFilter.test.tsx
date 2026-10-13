/** @jest-environment jsdom */
// 气泡版式同 dmore artboard-003「click筛选icon」:分组题「发起人」「会话」+ 分段控件,
// 选项为 aria-pressed 按钮(不再是 radio 列表),文案 我发起/他人发起/全部/已收藏。
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
    expect(screen.getByText('发起人')).toBeInTheDocument();
    expect(screen.getByText('会话')).toBeInTheDocument();
    expect(screen.getByText('已收藏')).toBeInTheDocument();
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
    expect(screen.getByText('发起人')).toBeInTheDocument();
    expect(screen.queryByText('会话')).not.toBeInTheDocument();
    expect(screen.queryByText('已收藏')).not.toBeInTheDocument();
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
    expect(screen.getByRole('button', { name: '我发起' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '他人发起' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: '已收藏' })).toHaveAttribute('aria-pressed', 'true');
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
    fireEvent.click(screen.getByRole('button', { name: '他人发起' }));
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
    fireEvent.click(screen.getByRole('button', { name: '已收藏' }));
    expect(onScopeChange).toHaveBeenCalledWith('favorite');
    expect(onOriginChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '全部' }));
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
  expect(screen.getByText('我发起')).toBeInTheDocument();
  expect(screen.getByText('他人发起')).toBeInTheDocument();
  expect(screen.queryByText('会话')).not.toBeInTheDocument();
  expect(screen.queryByText('全部')).not.toBeInTheDocument();
  expect(screen.queryByText('已收藏')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '发起归属' })).not.toHaveClass('bg-primary/10');
});

it('团队他人来源显示开发中且原生禁用，鼠标/键盘不触发选择；我的来源仍可选', () => {
  const handlers = callbacks();
  render(<ConversationOriginFilter botId="team:entity" origin="mine" scope="all" othersDisabled {...handlers} />);
  const others = screen.getByRole('button', { name: '他人发起' });
  expect(others).toBeDisabled();
  expect(others).toHaveClass('text-muted-foreground');
  fireEvent.click(others);
  fireEvent.keyDown(others, { key: 'Enter' });
  fireEvent.keyDown(others, { key: ' ' });
  expect(handlers.onOriginChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '我发起' }));
  expect(handlers.onOriginChange).toHaveBeenCalledWith('mine');
});