/** @jest-environment jsdom */
// 功能域：协作群列表渲染（群行样式/菜单/标签）与顶部视图切换、收起态。
// 从原 GroupSidebar.test.tsx 拆出（断言与渲染逻辑零改动），用于并行模式下多 worker 分摊。
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { baseGroup, makeProps, setupResizeObserverMock } from './GroupSidebar.testUtils';

beforeEach(setupResizeObserverMock);

describe('GroupSidebar', () => {
  it('empty state with create CTA when no groups', () => {
    const onCreateGroup = jest.fn();
    render(<GroupSidebar {...makeProps({ groups: [], onCreateGroup })} />);
    expect(screen.getByText(/暂无协作群/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('发起协作'));
    expect(onCreateGroup).toHaveBeenCalled();
  });

  it('群列表错误不伪装成空态，并提供重试入口', () => {
    const onRetryGroups = jest.fn().mockResolvedValue(undefined);
    render(<GroupSidebar {...makeProps({ groups: [], groupsError: '协作群加载失败', onRetryGroups })} />);
    expect(screen.getByRole('alert')).toHaveTextContent('协作群加载失败');
    expect(screen.queryByText('暂无协作群')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetryGroups).toHaveBeenCalledTimes(1);
  });

  it('协作群搜索工具行触发发起协作', () => {
    const onCreateGroup = jest.fn();
    render(<GroupSidebar {...makeProps({ onCreateGroup })} />);
    const actionButton = screen.getByRole('button', { name: '发起协作' });
    const searchInput = screen.getByRole('textbox', { name: '搜索协作群' });
    const toolRow = searchInput.parentElement?.parentElement;
    const tabRow = screen.getByRole('group', { name: '工作区类型' }).parentElement;
    expect(toolRow).toContainElement(actionButton);
    expect(tabRow).not.toContainElement(actionButton);
    fireEvent.click(actionButton);
    expect(onCreateGroup).toHaveBeenCalled();
  });

  it('群行将公开标签置于辅助信息首位，并移除成员数量展示', () => {
    render(
      <GroupSidebar
        {...makeProps({
          groups: [{ ...baseGroup, isPublic: true, participantCount: 6, kind: 'task_master_slave' as const }],
        })}
      />,
    );

    const groupTrigger = screen.getByRole('button', { name: '主站群' });
    expect(screen.getByText('主站群')).toHaveClass('text-sm', 'font-medium');
    expect(screen.getByText('主站群')).not.toHaveClass('font-semibold');
    expect(groupTrigger.textContent).toMatch(/主站群.*公开.*任务协作.*固定群成员/);
    expect(groupTrigger.textContent).not.toContain('6 个成员');
    expect(screen.queryByLabelText('6 个成员')).not.toBeInTheDocument();
  });

  it('协作群标签保留 aria 语义，不再提供 hover 提示（2026-09-13 用户决策移除徽标 Tooltip）', async () => {
    render(
      <GroupSidebar
        {...makeProps({
          groups: [{ ...baseGroup, isPublic: true, kind: 'task_dag' as const, membership: 'session_only' as const }],
        })}
      />,
    );

    // aria-label 保留无障碍语义；hover 后不出现 tooltip 气泡
    const metadata = screen.getByLabelText('协作群标签：公开 · 自定义协同 · 仅参与临时会话');
    expect(metadata).toHaveClass('truncate');
    await userEvent.setup().hover(metadata);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  it('v1.5：操作区绝对定位满行遮盖悬浮（badge/群名用满行宽），工具类底与行零色差', () => {
    render(
      <GroupSidebar
        {...makeProps({
          groups: [{ ...baseGroup, isPublic: true, kind: 'task_dag' as const, membership: 'session_only' as const }],
        })}
      />,
    );

    // 操作区脱离文档流（absolute）满行覆盖：inset-y-0 撑满行高（遮全 badge 文字），
    // 右缘止于箭头左缘；遮盖底走 global.css 工具类（真实合成零色差不透字）。
    // fixture 为展开态断 selected 分支；左缘 20px mask 渐隐；显隐语义保留（v1.4）。
    const actions = screen.getByRole('button', { name: '协作群操作' }).parentElement;
    expect(actions).toHaveClass('absolute', 'inset-y-0', 'right-[30px]', 'z-10');
    expect(actions).toHaveClass(
      'mask-[linear-gradient(to_right,transparent,black_20px)]',
      'sidebar-actions-cover-selected',
    );
    expect(actions).toHaveClass(
      'group-hover:opacity-100',
      'group-focus-within:opacity-100',
      '[@media(hover:none)]:opacity-100',
    );
  });

  it('v1.5：badge 行悬浮避让——hover 时右移让位操作区，单行 truncate 行高恒定', () => {
    render(
      <GroupSidebar
        {...makeProps({
          groups: [{ ...baseGroup, isPublic: true, kind: 'task_dag' as const, membership: 'session_only' as const }],
        })}
      />,
    );

    // 避让契约：badge 行保持单行 truncate（行高恒定不跳），行 hover 时右缘让出操作区宽度，
    // 悬浮按钮不再半透明叠在 badge 文字上（用户预览决策：换行展开观感乱，弃用）。
    const metadata = screen.getByLabelText('协作群标签：公开 · 自定义协同 · 仅参与临时会话');
    expect(metadata).toHaveClass('truncate', 'group-hover:pr-20');
    expect(metadata).not.toHaveClass('group-hover:flex-wrap');
  });

  it('clicking the group card toggles collapse (not only the chevron)', () => {
    const onSelectGroup = jest.fn();
    const onToggleGroupExpanded = jest.fn();
    render(<GroupSidebar {...makeProps({ onSelectGroup, onToggleGroupExpanded })} />);
    fireEvent.click(screen.getByText('主站群'));
    expect(onSelectGroup).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).toHaveBeenCalledWith('g1');
  });

  it('协作群管理菜单的每个操作都使用统一图标', () => {
    render(<GroupSidebar {...makeProps()} />);
    fireEvent.click(screen.getByRole('button', { name: '协作群操作' }));

    for (const label of ['管理协作群', '分享协作群', '解散协作群']) {
      expect(screen.getByRole('button', { name: label }).querySelector('svg')).toBeInTheDocument();
    }
  });

  it('群卡片移除展开箭头，新增入口与管理入口均不触发展开或选中', () => {
    const onCreateSession = jest.fn();
    const onManageGroup = jest.fn();
    const onToggleGroupExpanded = jest.fn();
    render(<GroupSidebar {...makeProps({ onCreateSession, onManageGroup, onToggleGroupExpanded })} />);
    expect(screen.getByRole('button', { name: '协作群操作' })).toHaveClass(
      'rounded-md',
      'hover:bg-primary/10',
      'hover:text-primary',
    );
    expect(
      screen.getByRole('button', { name: '主站群' }).querySelector('svg.lucide-chevron-right'),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '协作群操作' }));
    fireEvent.click(screen.getByRole('button', { name: '管理协作群' }));
    expect(onManageGroup).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
    const createSessionButton = screen.getByRole('button', { name: '新建会话' });
    const scopeButton = screen.getByRole('button', { name: '会话范围：全部会话' });
    expect(scopeButton).toHaveClass('h-6', 'w-6');
    expect(scopeButton.querySelector('svg.lucide-list-filter')).toBeInTheDocument();
    fireEvent.click(createSessionButton);
    fireEvent.click(screen.getByText('参与者视角'));
    expect(onCreateSession).toHaveBeenCalledWith('g1', 'participant');
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
  });

  it('群间列表无分割线与容器底色（验收微调：通透列表风格）', () => {
    const secondGroup = { ...baseGroup, groupId: 'g2', name: '新品发布协作组', sessions: [] };
    render(<GroupSidebar {...makeProps({ groups: [baseGroup, secondGroup] })} />);

    const sessionList = screen.getByLabelText('协作群会话列表：主站群');
    const groupList = sessionList.parentElement?.parentElement;
    expect(groupList).not.toHaveClass('divide-y', 'divide-border/70', 'bg-muted/10', 'border-b');
    expect(sessionList).not.toHaveClass('border-t', 'border-b', 'ml-[60px]', 'border-l', 'pl-2');
    expect(sessionList.firstElementChild).not.toHaveClass('border-b');
  });

  it('clicking a session does not collapse its group (no bubble to card)', () => {
    const onToggleGroupExpanded = jest.fn();
    render(<GroupSidebar {...makeProps({ onToggleGroupExpanded })} />);
    fireEvent.click(screen.getByText('会话一'));
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
  });

  it('群名称搜索框保留 focus ring 的左侧可视空间', () => {
    render(<GroupSidebar {...makeProps()} />);
    const searchInput = screen.getByRole('textbox', { name: '搜索协作群' });
    expect(searchInput.parentElement?.parentElement).toHaveClass('px-4');
  });

  it('顶部视图切换只承担导航，未选中 Tab 保持清晰对比', () => {
    render(<GroupSidebar {...makeProps()} />);
    const inactiveTab = screen.getByRole('button', { name: '对话' });
    const activeTab = screen.getByRole('button', { name: '协作群' });
    const actionButton = screen.getByRole('button', { name: '发起协作' });
    const tabRow = screen.getByRole('group', { name: '工作区类型' }).parentElement;
    expect(inactiveTab).toHaveAttribute('aria-pressed', 'false');
    expect(activeTab).toHaveAttribute('aria-pressed', 'true');
    expect(activeTab).toHaveClass('bg-background', 'text-primary', 'shadow-sm');
    expect(inactiveTab).toHaveClass('text-muted-foreground');
    expect(tabRow).not.toContainElement(actionButton);
    expect(actionButton).toHaveClass('h-9', 'w-9', 'rounded-md');
    expect(actionButton).toHaveClass('border-primary/20', 'bg-primary/5', 'text-primary');
    expect(actionButton).not.toHaveClass('bg-primary', 'text-primary-foreground');
    expect(actionButton).not.toHaveClass('lg:hidden');
  });

  it('群名称搜索框、筛选按钮与发起协作按钮等高', () => {
    render(<GroupSidebar {...makeProps()} />);
    expect(screen.getByRole('textbox', { name: '搜索协作群' })).toHaveClass('h-9');
    expect(screen.getByRole('button', { name: '筛选' })).toHaveClass('h-9');
    expect(screen.getByRole('button', { name: '发起协作' })).toHaveClass('h-9');
  });

  it('协作身份移出二级侧栏，群卡片保留可读间距并降低标题字重', () => {
    render(<GroupSidebar {...makeProps()} />);
    expect(screen.queryByRole('button', { name: '当前协作身份：示例用户' })).not.toBeInTheDocument();
    const groupTrigger = screen.getByRole('button', { name: /主站群/ });
    expect(groupTrigger.parentElement).toHaveClass('min-h-16', 'bg-muted', 'px-4', 'py-2.5');
    expect(groupTrigger).toHaveClass('px-0', 'py-1');
    expect(screen.getByText('主站群')).toHaveClass('text-sm', 'font-medium');
    expect(screen.getByText('主站群')).not.toHaveClass('font-semibold');
    expect(screen.getByText('自由聊天').parentElement).toHaveClass('text-xs', 'leading-4');
  });

  it('v1.4：展开/选中群行头像品牌浅底弱化强调', () => {
    const { unmount } = render(<GroupSidebar {...makeProps()} />);
    const groupTrigger = screen.getByRole('button', { name: /主站群/ });
    const avatar = groupTrigger.querySelector('svg.lucide-users')?.parentElement as HTMLElement;
    expect(avatar).toHaveClass('bg-primary/15', 'text-primary', 'ring-primary/30');
    unmount();

    render(<GroupSidebar {...makeProps({ expandedGroupIds: {}, selectedGroupId: null })} />);
    const idleTrigger = screen.getByRole('button', { name: /主站群/ });
    const idleAvatar = idleTrigger.querySelector('svg.lucide-users')?.parentElement as HTMLElement;
    expect(idleAvatar).toHaveClass('bg-secondary', 'text-secondary-foreground', 'ring-border');
  });

  it('v1.4：群行操作区默认透明隐藏，悬停/键盘聚焦可显现，选中或展开时常显', () => {
    const { rerender } = render(<GroupSidebar {...makeProps({ expandedGroupIds: {}, selectedGroupId: null })} />);
    const actions = screen.getByRole('button', { name: '协作群操作' }).parentElement as HTMLElement;
    expect(actions).toHaveClass(
      'opacity-0',
      'transition-opacity',
      'group-hover:opacity-100',
      'group-focus-within:opacity-100',
    );
    expect(actions).not.toHaveClass('opacity-100');

    rerender(<GroupSidebar {...makeProps({ expandedGroupIds: {}, selectedGroupId: 'g1' })} />);
    const actionsSelected = screen.getByRole('button', { name: '协作群操作' }).parentElement as HTMLElement;
    expect(actionsSelected).toHaveClass('opacity-100');

    rerender(
      <GroupSidebar
        {...makeProps({
          expandedGroupIds: {},
          selectedGroupId: null,
          sessionTabsByGroup: { g1: 'favorite' },
        })}
      />,
    );
    const actionsFavorite = screen.getByRole('button', { name: '协作群操作' }).parentElement as HTMLElement;
    expect(actionsFavorite).toHaveClass('opacity-100');
  });

  it('协作群列表向下滚动时一级 Tab 吸顶', () => {
    render(<GroupSidebar {...makeProps()} />);

    const tabGroup = screen.getByRole('group', { name: '工作区类型' });
    expect(tabGroup.parentElement?.parentElement).toHaveClass('sticky', 'top-0', 'z-20', 'bg-muted/20');
  });

  it('收起态保留对话与协作群快捷切换图标', () => {
    const onViewChange = jest.fn();
    render(<GroupSidebar {...makeProps({ onViewChange })} />);

    fireEvent.click(screen.getByRole('button', { name: '收起对话协作左栏' }));

    expect(screen.getByRole('button', { name: '切换到对话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '切换到协作群' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: '切换到对话' }));
    expect(onViewChange).toHaveBeenCalledWith('chat');
    fireEvent.click(screen.getByRole('button', { name: '展开对话协作左栏' }));
  });
});
