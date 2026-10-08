/** @jest-environment jsdom */
// 功能域：筛选按钮 Popover 与会话范围（全部/已收藏）Popover 的交互行为。
// 从原 GroupSidebar.test.tsx 拆出（断言与渲染逻辑零改动），用于并行模式下多 worker 分摊。
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { makeProps, setupResizeObserverMock } from './GroupSidebar.testUtils';

// 筛选/会话范围 Popover 交互重度依赖 Radix 异步渲染：并行全量下 30s 随机超时，60s 仍临界，
// 本文件统一放宽至 90s（仅本文件生效，不改全局 testTimeout）。
jest.setTimeout(90000);

beforeEach(setupResizeObserverMock);

describe('GroupSidebar', () => {
  it('filter panel renders participation and all 4 group-kind options', () => {
    render(<GroupSidebar {...makeProps()} />);
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));
    expect(screen.getByRole('radio', { name: '固定群成员' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '仅参与临时会话' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '全部' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '自由聊天' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '任务协作' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '自定义协同' })).toBeInTheDocument();
    expect(screen.getAllByRole('radiogroup').map((group) => group.getAttribute('aria-label'))).toEqual([
      '协作群类型',
      '协作群参与方式',
    ]);
    const filterPanel = screen.getByRole('radiogroup', { name: '协作群参与方式' }).parentElement;
    expect(filterPanel).toHaveClass(
      'w-[360px]',
      'max-w-[calc(100vw-1rem)]',
      'rounded-lg',
      'border',
      'bg-popover',
      'p-3',
      'shadow-md',
    );
    expect(filterPanel).not.toHaveClass('mx-[18px]', 'mt-2');
    const sidebarScrollArea =
      screen.getByLabelText('协作群会话列表：主站群').parentElement?.parentElement?.parentElement;
    expect(sidebarScrollArea).not.toContainElement(filterPanel);
    expect(screen.getByRole('radiogroup', { name: '协作群类型' })).toHaveClass('min-w-0');
    expect(screen.getByRole('radiogroup', { name: '协作群类型' }).querySelector('.flex')).toHaveClass(
      'min-w-0',
      'flex-wrap',
      'gap-1',
    );
    expect(screen.getByRole('radiogroup', { name: '协作群参与方式' })).toHaveClass('min-w-0', 'border-t', 'pt-3');
    expect(screen.getByRole('radiogroup', { name: '协作群参与方式' }).querySelector('.flex')).toHaveClass(
      'min-w-0',
      'flex-wrap',
      'gap-1',
    );
    expect(screen.getByRole('radio', { name: '全部' }).querySelector('svg')).toBeInTheDocument();
  });

  it('renders explicit participation labels and clicking temporary-session participation fires onMembershipChange', () => {
    const onMembershipChange = jest.fn();
    render(<GroupSidebar {...makeProps({ membership: 'direct', onMembershipChange })} />);
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));
    const directBtn = screen.getByRole('radio', { name: '固定群成员' });
    const sessionBtn = screen.getByRole('radio', { name: '仅参与临时会话' });
    expect(directBtn).toBeInTheDocument();
    expect(sessionBtn).toBeInTheDocument();
    fireEvent.click(sessionBtn);
    expect(onMembershipChange).toHaveBeenCalledWith('session_only');
  });

  it('会话范围使用对象行纯 Icon，选项在 Popover 中展示', () => {
    render(<GroupSidebar {...makeProps({ totalSessionsByGroupId: { g1: 12 } })} />);

    const scopeButton = screen.getByRole('button', { name: '会话范围：全部会话' });
    expect(scopeButton.textContent).toBe('');
    fireEvent.click(scopeButton);
    expect(screen.getByRole('radiogroup', { name: '会话范围' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '全部会话 12' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '已收藏会话 0' })).toBeInTheDocument();
  });

  it('renders all and favorite session counts beside collaboration session tabs', () => {
    render(
      <GroupSidebar
        {...makeProps({
          totalSessionsByGroupId: { g1: 12 },
          favoriteSessionIds: ['s1'],
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '会话范围：全部会话' }));
    expect(screen.getByRole('radio', { name: '全部会话 12' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '已收藏会话 1' })).toBeInTheDocument();
  });

  it('群会话总数未知时使用占位符，不用当前已加载条数冒充总数', () => {
    render(<GroupSidebar {...makeProps({ favoriteSessionIds: ['s1'] })} />);

    fireEvent.click(screen.getByRole('button', { name: '会话范围：全部会话' }));
    expect(screen.getByRole('radio', { name: '全部会话 …' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '已收藏会话 1' })).toBeInTheDocument();
  });

  it('does not present the loaded-page favorite count as a total while more group sessions remain', () => {
    render(
      <GroupSidebar
        {...makeProps({
          favoriteSessionIds: ['s1'],
          hasMoreSessionsByGroupId: { g1: true },
        })}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '会话范围：全部会话' }));
    expect(screen.getByRole('radio', { name: '全部会话 …' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '已收藏会话 …' })).toBeInTheDocument();
  });

  it('closes the filter panel after selecting a filter', () => {
    const onMembershipChange = jest.fn();
    render(<GroupSidebar {...makeProps({ onMembershipChange })} />);
    const filterButton = screen.getByRole('button', { name: '筛选' });
    fireEvent.click(filterButton);
    fireEvent.click(screen.getByRole('radio', { name: '仅参与临时会话' }));
    expect(onMembershipChange).toHaveBeenCalledWith('session_only');
    expect(screen.queryByRole('radio', { name: '仅参与临时会话' })).not.toBeInTheDocument();
    expect(filterButton).toHaveAttribute('aria-expanded', 'false');
  });

  it('再次点击筛选按钮可以收起筛选面板', () => {
    render(<GroupSidebar {...makeProps()} />);
    const filterButton = screen.getByRole('button', { name: '筛选' });
    fireEvent.click(filterButton);
    expect(filterButton).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(filterButton);
    expect(filterButton).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('radio', { name: '全部' })).not.toBeInTheDocument();
  });

  it('closes the filter panel on outside pointer or Escape', async () => {
    const user = userEvent.setup();
    render(<GroupSidebar {...makeProps()} />);
    const filterButton = screen.getByRole('button', { name: '筛选' });
    await user.click(filterButton);
    expect(screen.getByRole('radio', { name: '全部' })).toBeInTheDocument();
    // Radix Popover 的 outside click 检测依赖 pointerdown 事件；
    // userEvent.click(document.body) 在 jsdom 下会挂起不返回，改用 fireEvent。
    fireEvent.pointerDown(document.body);
    fireEvent.pointerUp(document.body);
    expect(screen.queryByRole('radio', { name: '全部' })).not.toBeInTheDocument();

    await user.click(filterButton);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('radio', { name: '全部' })).not.toBeInTheDocument();
  }, 90000);

  it('marks the filter button when a filter is applied', () => {
    render(<GroupSidebar {...makeProps({ membership: 'session_only' })} />);
    const filterButton = screen.getByRole('button', { name: '筛选' });
    expect(filterButton).toHaveAttribute('aria-pressed', 'true');
    expect(filterButton).toHaveClass('bg-primary/5');
  });
});
