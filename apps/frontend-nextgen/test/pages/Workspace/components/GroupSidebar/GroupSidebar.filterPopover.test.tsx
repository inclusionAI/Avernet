/** @jest-environment jsdom */
// 功能域：v2 筛选弹层（ListFilter icon + Segmented，dmore artboard-010 搜索行右侧）与
// 会话范围（全部/已收藏）Popover 的交互行为。筛选形态对齐对话页 ConversationOriginFilter：
// 选项为 aria-pressed 按钮（不再是 radio 列表），触发器为「筛选协作群」图标按钮。
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
    fireEvent.click(screen.getByRole('button', { name: '筛选协作群' }));
    expect(screen.getByRole('button', { name: '固定群成员' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '仅参与临时会话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '全部' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '自由聊天' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '任务协作' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '自定义协同' })).toBeInTheDocument();
    expect(screen.getAllByRole('group').map((group) => group.getAttribute('aria-label'))).toContain(
      '协作群类型',
    );
    expect(screen.getAllByRole('group').map((group) => group.getAttribute('aria-label'))).toContain(
      '参与方式',
    );
    // 分段默认选中态：全部（类型）与固定群成员（参与方式）。
    expect(screen.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '自由聊天' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: '固定群成员' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('renders explicit participation labels and clicking temporary-session participation fires onMembershipChange', () => {
    const onMembershipChange = jest.fn();
    render(<GroupSidebar {...makeProps({ membership: 'direct', onMembershipChange })} />);
    fireEvent.click(screen.getByRole('button', { name: '筛选协作群' }));
    const directBtn = screen.getByRole('button', { name: '固定群成员' });
    const sessionBtn = screen.getByRole('button', { name: '仅参与临时会话' });
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
    const filterButton = screen.getByRole('button', { name: '筛选协作群' });
    fireEvent.click(filterButton);
    fireEvent.click(screen.getByRole('button', { name: '仅参与临时会话' }));
    expect(onMembershipChange).toHaveBeenCalledWith('session_only');
    // forceMount 气泡关闭时仅以 hidden 收起：选项退出可访问树（role 查询不可见）。
    expect(screen.queryByRole('button', { name: '仅参与临时会话' })).not.toBeInTheDocument();
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

  it('marks the filter button when a filter is applied', () => {
    render(<GroupSidebar {...makeProps({ membership: 'session_only' })} />);
    // 激活态读屏名带「已有生效筛选」后缀（对齐触发器 ariaLabel 契约）。
    const filterButton = screen.getByRole('button', { name: '筛选协作群（已有生效筛选）' });
    expect(filterButton).toHaveAttribute('aria-pressed', 'true');
    expect(filterButton).toHaveClass('text-primary');
  });
});
