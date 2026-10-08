/** @jest-environment jsdom */
// 功能域：会话收藏星标交互与收藏（favorite）会话范围过滤/空态。
// 从原 GroupSidebar.test.tsx 拆出（断言与渲染逻辑零改动），用于并行模式下多 worker 分摊。
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

import { baseGroup, makeProps, setupResizeObserverMock } from './GroupSidebar.testUtils';

beforeEach(setupResizeObserverMock);

describe('GroupSidebar', () => {
  it('v1.4：已收藏星标常显、未收藏星标默认隐藏，点击切换且不触发行选中', () => {
    const onToggleFavorite = jest.fn();
    const onSelectSession = jest.fn();
    const sessions = [{ ...baseGroup.sessions[0], favorite: false }];
    render(
      <GroupSidebar
        {...makeProps({
          sessionsByGroupId: { g1: sessions },
          favoriteSessionIds: ['s1'],
          onToggleFavorite,
          onSelectSession,
        })}
      />,
    );

    // 已收藏：星标常显（无需悬停），无隐藏态 class
    const star = screen.getByRole('button', { name: '取消收藏' });
    expect(star.querySelector('svg.lucide-star')).toBeInTheDocument();
    expect(star).not.toHaveClass('opacity-0');
    fireEvent.click(star);
    expect(onToggleFavorite).toHaveBeenCalledWith('s1');
    expect(onSelectSession).not.toHaveBeenCalled();
  });

  it('v1.4：未收藏星标默认隐藏，悬停/键盘聚焦时显现', () => {
    render(<GroupSidebar {...makeProps()} />);
    const stars = screen.getAllByRole('button', { name: '收藏会话' });
    for (const star of stars) {
      expect(star).toHaveClass('opacity-0', 'group-hover:opacity-100', 'group-focus-within:opacity-100');
    }
  });

  it('v1.4：群会话未收藏星标隐藏态仍可交互，更多菜单不再承载收藏项', () => {
    render(<GroupSidebar {...makeProps()} />);
    const stars = screen.getAllByRole('button', { name: '收藏会话' });
    expect(stars[0].querySelector('svg.lucide-star')).toBeInTheDocument();
    expect(stars[0]).toHaveClass('opacity-0');

    fireEvent.click(screen.getAllByRole('button', { name: '会话更多操作' })[0]);
    expect(screen.getByRole('button', { name: '管理会话' }).querySelector('svg')).toBeInTheDocument();
    // 菜单打开后收藏入口数量不变（仅行上星标，菜单内无收藏项）
    expect(screen.getAllByRole('button', { name: '收藏会话' })).toHaveLength(stars.length);
  });

  it('对象行会话范围 Icon 按群过滤收藏会话', () => {
    const onSessionTabForGroup = jest.fn();
    render(
      <GroupSidebar
        {...makeProps({
          sessionTabsByGroup: { g1: 'favorite' },
          favoriteSessionIds: ['s1'],
          onSessionTabForGroup,
        })}
      />,
    );
    expect(screen.getByText('会话一')).toBeInTheDocument();
    expect(screen.queryByText('会话二')).not.toBeInTheDocument();
    const scopeButton = screen.getByRole('button', { name: '会话范围：已收藏会话' });
    expect(scopeButton).toHaveAttribute('aria-pressed', 'true');
    expect(scopeButton.textContent).toBe('');
    fireEvent.click(scopeButton);
    expect(screen.getByRole('radio', { name: /已收藏会话/ })).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('radio', { name: /全部会话/ }));
    expect(onSessionTabForGroup).toHaveBeenCalledWith('g1', 'all');
  });

  it('收起协作群切换会话范围后自动展开对象', () => {
    const onSessionTabForGroup = jest.fn();
    const onToggleGroupExpanded = jest.fn();
    render(
      <GroupSidebar
        {...makeProps({
          expandedGroupIds: {},
          onSessionTabForGroup,
          onToggleGroupExpanded,
        })}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '会话范围：全部会话' }));
    fireEvent.click(screen.getByRole('radio', { name: '已收藏会话 0' }));

    expect(onSessionTabForGroup).toHaveBeenCalledWith('g1', 'favorite');
    expect(onToggleGroupExpanded).toHaveBeenCalledWith('g1');
  });

  it('收藏范围仅检查已加载分页时展示明确空态', () => {
    render(
      <GroupSidebar
        {...makeProps({
          sessionTabsByGroup: { g1: 'favorite' },
          favoriteSessionIds: [],
          hasMoreSessionsByGroupId: { g1: true },
        })}
      />,
    );

    expect(screen.getByText('当前已加载会话中暂无收藏')).toBeInTheDocument();
  });
});
