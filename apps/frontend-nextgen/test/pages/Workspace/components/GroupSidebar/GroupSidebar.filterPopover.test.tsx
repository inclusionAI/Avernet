/** @jest-environment jsdom */
// 功能域：搜索下方参与方式 Tab、群类型筛选弹层（ListFilter icon + Segmented）与
// 会话范围（全部/已收藏）Popover 的交互行为。筛选形态对齐对话页 ConversationOriginFilter：
// 选项为 aria-pressed 按钮（不再是 radio 列表），触发器为「筛选协作群」图标按钮。
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { makeProps, setupResizeObserverMock } from './GroupSidebar.testUtils';

// 筛选/会话范围 Popover 交互重度依赖 Radix 异步渲染：并行全量下 30s 随机超时，60s 仍临界，
// 本文件统一放宽至 90s（仅本文件生效，不改全局 testTimeout）。
jest.setTimeout(90000);

beforeEach(setupResizeObserverMock);

describe('GroupSidebar', () => {
  it('keeps participation tabs below search and outside the group-kind popover', () => {
    render(<GroupSidebar {...makeProps()} />);
    const tabs = screen.getByRole('tablist', { name: '参与方式' });
    const searchRow = screen.getByRole('textbox', { name: '搜索协作群' }).parentElement?.parentElement;
    expect(searchRow?.nextElementSibling).toBe(tabs);
    expect(tabs.parentElement).toHaveClass('sticky');
    expect(tabs).toHaveClass('grid-cols-2');
    expect(within(tabs).getAllByRole('tab')).toHaveLength(2);
    expect(screen.getByRole('tab', { name: '固定群成员' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '仅参与临时会话' })).toHaveAttribute('aria-selected', 'false');

    fireEvent.click(screen.getByRole('button', { name: '筛选协作群' }));
    const kindGroup = screen.getByRole('group', { name: '协作群类型' });
    for (const name of ['全部', '自由聊天', '任务协作', '自定义协同']) {
      expect(within(kindGroup).getByRole('button', { name })).toBeInTheDocument();
    }
    expect(screen.queryByRole('group', { name: '参与方式' })).not.toBeInTheDocument();
    expect(kindGroup.parentElement?.parentElement).not.toContainElement(tabs);
    expect(within(kindGroup).getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(kindGroup).getByRole('button', { name: '自由聊天' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('participation tabs switch both ways without opening the popover or changing the kind filter', () => {
    const props = makeProps();
    const view = render(<GroupSidebar {...props} />);
    fireEvent.click(screen.getByRole('tab', { name: '仅参与临时会话' }));
    expect(props.onMembershipChange).toHaveBeenLastCalledWith('session_only');
    expect(props.onKindFilterChange).not.toHaveBeenCalled();
    expect(screen.queryByRole('group', { name: '协作群类型' })).not.toBeInTheDocument();

    view.rerender(<GroupSidebar {...props} membership="session_only" />);
    expect(screen.getByRole('tab', { name: '仅参与临时会话' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '固定群成员' })).toHaveAttribute('aria-selected', 'false');
    fireEvent.click(screen.getByRole('tab', { name: '固定群成员' }));
    expect(props.onMembershipChange).toHaveBeenLastCalledWith('direct');
  });

  it('restricted group views do not expose membership tabs or cross-group filters', () => {
    render(<GroupSidebar {...makeProps({ restricted: true })} />);
    expect(screen.queryByRole('tablist', { name: '参与方式' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /筛选协作群/ })).not.toBeInTheDocument();
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

  it('closes the filter panel after selecting a group kind, while participation tabs remain visible', () => {
    const onKindFilterChange = jest.fn();
    render(<GroupSidebar {...makeProps({ onKindFilterChange })} />);
    fireEvent.click(screen.getByRole('button', { name: '筛选协作群' }));
    fireEvent.click(screen.getByRole('button', { name: '任务协作' }));
    expect(onKindFilterChange).toHaveBeenCalledWith('task_master_slave');
    expect(screen.queryByRole('button', { name: '任务协作' })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '固定群成员' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '仅参与临时会话' })).toBeInTheDocument();
  });

  it('再次点击筛选按钮可以收起筛选面板', () => {
    render(<GroupSidebar {...makeProps()} />);
    const filterButton = screen.getByRole('button', { name: '筛选协作群' });
    fireEvent.click(filterButton);
    expect(filterButton).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(filterButton);
    expect(filterButton).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: '全部' })).not.toBeInTheDocument();
  });

  it('closes the filter panel on outside pointer or Escape', async () => {
    const user = userEvent.setup();
    render(<GroupSidebar {...makeProps()} />);
    const filterButton = screen.getByRole('button', { name: '筛选协作群' });
    await user.click(filterButton);
    expect(screen.getByRole('button', { name: '全部' })).toBeInTheDocument();
    // Radix Popover 的 outside click 检测依赖 pointerdown 事件；
    // userEvent.click(document.body) 在 jsdom 下会挂起不返回，改用 fireEvent。
    fireEvent.pointerDown(document.body);
    fireEvent.pointerUp(document.body);
    expect(screen.queryByRole('button', { name: '全部' })).not.toBeInTheDocument();

    await user.click(filterButton);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('button', { name: '全部' })).not.toBeInTheDocument();
  }, 90000);

  it('marks the filter button only when a group-kind filter is applied', () => {
    render(<GroupSidebar {...makeProps({ kindFilter: 'task_master_slave' })} />);
    // 激活态读屏名带「已有生效筛选」后缀（对齐触发器 ariaLabel 契约）。
    const filterButton = screen.getByRole('button', { name: '筛选协作群（已有生效筛选）' });
    expect(filterButton).toHaveAttribute('aria-pressed', 'true');
    expect(filterButton).toHaveClass('text-primary');
  });
  it('does not mark the group-kind filter when only temporary-session participation is selected', () => {
    render(<GroupSidebar {...makeProps({ membership: 'session_only' })} />);
    const filterButton = screen.getByRole('button', { name: '筛选协作群' });
    expect(filterButton).toHaveAttribute('aria-pressed', 'false');
    expect(filterButton).not.toHaveClass('text-primary');
  });
});
