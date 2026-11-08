/** @jest-environment jsdom */
// 功能域：协作群列表渲染（群行样式/菜单/标签）;视图切换已收口到 App Shell(Task 8)。
// 从原 GroupSidebar.test.tsx 拆出（断言与渲染逻辑零改动），用于并行模式下多 worker 分摊。
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

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

  it('v2：发起协作「+」常驻搜索行（设计稿 x=251），无「协作群」组标题行', () => {
    const onCreateGroup = jest.fn();
    render(<GroupSidebar {...makeProps({ onCreateGroup })} />);
    const actionButton = screen.getByRole('button', { name: '发起协作' });
    const searchInput = screen.getByRole('textbox', { name: '搜索协作群' });
    // 「+」位于搜索工具行（input 的 relative 包裹层的父行）。
    const toolRow = searchInput.closest('div.relative')?.parentElement;
    expect(toolRow).toContainElement(actionButton);
    // 设计稿搜索行下直接是群列表，无组标题行。
    expect(screen.queryByText('协作群')).not.toBeInTheDocument();
    fireEvent.click(actionButton);
    expect(onCreateGroup).toHaveBeenCalled();
  });

  it('群行 52px 双行形态，仅保留 kind 标签（公开/参与方式徽章移除）', () => {
    render(
      <GroupSidebar
        {...makeProps({
          groups: [{ ...baseGroup, isPublic: true, participantCount: 6, kind: 'task_master_slave' as const }],
        })}
      />,
    );

    const groupTrigger = screen.getByRole('button', { name: '主站群' });
    expect(groupTrigger.textContent).toMatch(/主站群.*任务协作/);
    expect(groupTrigger.textContent).not.toContain('公开');
    expect(groupTrigger.textContent).not.toContain('固定群成员');
    expect(groupTrigger.textContent).not.toContain('6 个成员');
    // 名称和类型分两行，与对话 Bot 行统一为 52px。
    expect(groupTrigger).toHaveClass('min-h-13');
  });

  it('v2.1：群行首恒为成员头像堆（≤2 + 溢出徽标），无文件夹图标（用户反馈还原设计稿）', () => {
    const participants = Array.from({ length: 5 }, (_, i) => ({
      actorId: `actor-${i}`,
      kind: 'bot' as const,
      name: `成员${i}`,
      role: 'member' as const,
      mode: 'auto' as const,
    }));
    const first = render(
      <GroupSidebar
        {...makeProps({
          groups: [{ ...baseGroup, participants: [...participants], participantCount: 8, sessions: [] }],
          expandedGroupIds: { g1: true },
          sessionsByGroupId: { g1: [] },
        })}
      />,
    );
    // 展开态：≤2 个头像 + 8-2=6 溢出徽标「6+」（设计稿 artboard-010「运营协作」行同款）。
    const expandedTrigger = screen.getByRole('button', { name: '主站群' });
    const stack = expandedTrigger.querySelector('[aria-label="成员 8 人"]');
    expect(stack?.children).toHaveLength(3);
    expect(stack?.lastElementChild).toHaveTextContent('6+');
    expect(expandedTrigger.querySelector('svg.lucide-folder')).not.toBeInTheDocument();
    first.unmount();

    // 收起态：行首同样渲染头像堆（设计稿「群目录一级_收起」行行首即头像堆，非文件夹）。
    const second = render(
      <GroupSidebar
        {...makeProps({
          groups: [{ ...baseGroup, participants: [...participants], participantCount: 8, sessions: [] }],
          expandedGroupIds: {},
          sessionsByGroupId: { g1: [] },
        })}
      />,
    );
    const collapsedTrigger = screen.getByRole('button', { name: '主站群' });
    expect(collapsedTrigger.querySelector('[aria-label="成员 8 人"]')).toBeInTheDocument();
    expect(collapsedTrigger.querySelector('svg.lucide-folder')).not.toBeInTheDocument();
    // 收起态箭头常驻 + -rotate-90（2026-10-22 对话侧同语言）：替代原「仅展开态显示」，
    // 收起行也有「可展开」的可点暗示，过渡由 rotate 承载而非显隐硬切换。
    const collapsedArrow = collapsedTrigger.querySelector('svg.lucide-chevron-down') as HTMLElement;
    expect(collapsedArrow).toBeInTheDocument();
    expect(collapsedArrow).toHaveClass('-rotate-90');
    second.unmount();

    // participants 未回填（列表补拉未完成/失败）：不渲染任何行首占位，也不用文件夹兜底。
    render(
      <GroupSidebar
        {...makeProps({ groups: [{ ...baseGroup, sessions: [] }], expandedGroupIds: {}, sessionsByGroupId: { g1: [] } })}
      />,
    );
    const emptyTrigger = screen.getByRole('button', { name: '主站群' });
    expect(emptyTrigger.querySelector('svg.lucide-folder')).not.toBeInTheDocument();
    expect(emptyTrigger.querySelector('[aria-label^="成员"]')).not.toBeInTheDocument();
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

  it('群行 dots 菜单操作不触发展开或选中，展开态 chevron-down 可见', () => {
    const onCreateSession = jest.fn();
    const onManageGroup = jest.fn();
    const onToggleGroupExpanded = jest.fn();
    render(
      <GroupSidebar
        {...makeProps({ onCreateSession, onManageGroup, onToggleGroupExpanded, sessionsByGroupId: { g1: [] } })}
      />,
    );
    const dots = screen.getByRole('button', { name: '协作群操作' });
    expect(dots).toHaveClass('h-6', 'w-6', 'rounded-md');
    // 展开态行内 chevron 归零（旋转语言，非显隐切换）。
    const expandedArrow = screen.getByRole('button', { name: '主站群' }).querySelector(
      'svg.lucide-chevron-down',
    ) as HTMLElement;
    expect(expandedArrow).toBeInTheDocument();
    expect(expandedArrow).not.toHaveClass('-rotate-90');
    fireEvent.click(dots);
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
    // 2026-10-22 对话侧同语言：一级行行尾的逐行分割线一并移除，行间只靠留白分隔。
    expect(sessionList.parentElement?.querySelectorAll('.border-b')).toHaveLength(0);
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

  it('v2：搜索框 h-8 视觉规范，筛选与发起协作图标按钮 32px 等高（收起走 shell 边缘 chevron）', () => {
    render(<GroupSidebar {...makeProps()} />);
    expect(screen.getByRole('textbox', { name: '搜索协作群' })).toHaveClass('h-8');
    expect(screen.getByRole('button', { name: '筛选协作群' })).toHaveClass('h-8', 'w-8');
    // 发起协作「+」：白底描边方块，与搜索框同一视觉语言（artboard-010 x=251）。
    const createButton = screen.getByRole('button', { name: '发起协作' });
    expect(createButton).toHaveClass('h-8', 'w-8', 'rounded-lg', 'border-input', 'bg-background');
    // 搜索行内不再有收起按钮。
    expect(screen.queryByRole('button', { name: '收起侧边栏' })).not.toBeInTheDocument();
  });

  it('协作身份区位于群搜索工具行之前（v2 行几何与身份行同壳）', () => {
    render(<GroupSidebar {...makeProps()} />);
    const identityRegion = screen.getByRole('region', { name: '当前协作身份' });
    const searchInput = screen.getByRole('textbox', { name: '搜索协作群' });
    const scrollContainer = searchInput.closest('.app-scrollbar');
    const identityWrapper = identityRegion.parentElement;
    const position = identityRegion.compareDocumentPosition(searchInput);
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(identityRegion.closest('.app-scrollbar')).toBeNull();
    expect(identityWrapper).toHaveClass('shrink-0');
    expect(identityWrapper?.parentElement).toBe(scrollContainer?.parentElement);
    expect(scrollContainer).toHaveClass('app-scrollbar', 'min-h-0', 'flex-1', 'overflow-y-auto');
    expect(scrollContainer?.firstElementChild).not.toBe(identityRegion);
    // 群一级行：52px 双行布局，行背景胶囊与按钮热区分离。
    const groupRow = screen.getByRole('button', { name: /主站群/ }).closest('.group\\/row');
    expect(groupRow).toHaveClass('relative', 'flex', 'min-h-13');
    expect(screen.getByRole('button', { name: /主站群/ })).toHaveClass('min-h-13');
    // 群名规格与会话标题一致（2026-10-22，对齐对话侧 Bot 名同轮裁决）：13px 常规字重，
    // 一级/二级只靠头像堆与缩进分层，目录不靠加粗强调。
    expect(screen.getByText('主站群')).toHaveClass('text-[13px]', 'font-normal');
  });

  it('v2.1：群行无选中态（用户反馈移除）——选中群胶囊恒为 hover 弱底，不再落 bg-selected', () => {
    const { rerender } = render(<GroupSidebar {...makeProps({ expandedGroupIds: {} })} />);
    const capsule = screen.getByRole('button', { name: /主站群/ }).previousElementSibling as HTMLElement;
    expect(capsule).toHaveClass('left-2', 'right-2', 'rounded-md', 'bg-muted/60', 'opacity-0');
    expect(capsule).not.toHaveClass('opacity-100');
    expect(capsule).not.toHaveClass('w-[3px]', 'rounded-r-sm');

    rerender(<GroupSidebar {...makeProps({ selectedGroupId: 'g1', expandedGroupIds: {} })} />);
    const capsuleSelected = screen.getByRole('button', { name: /主站群/ }).previousElementSibling as HTMLElement;
    expect(capsuleSelected).not.toHaveClass('bg-selected');
    expect(capsuleSelected).toHaveClass('opacity-0');
  });

  it('v2.1：群行操作区默认透明隐藏，悬停/键盘聚焦可显现；常显（展开/收藏 Tab）须与标题让位同步防重叠，选中不再常显', () => {
    const { rerender } = render(<GroupSidebar {...makeProps({ expandedGroupIds: {}, selectedGroupId: null })} />);
    const actions = screen.getByRole('button', { name: '协作群操作' }).parentElement?.parentElement as HTMLElement;
    expect(actions).toHaveClass('opacity-0', 'transition-opacity');
    expect(actions.className).toContain('group-hover/row:opacity-100');
    expect(actions.className).toContain('group-focus-within/row:opacity-100');
    expect(actions).not.toHaveClass('opacity-100');

    // 选中态已移除（用户反馈）：不常显、标题不让位。
    rerender(<GroupSidebar {...makeProps({ expandedGroupIds: {}, selectedGroupId: 'g1' })} />);
    const actionsSelected = screen.getByRole('button', { name: '协作群操作' }).parentElement?.parentElement as HTMLElement;
    expect(actionsSelected).not.toHaveClass('opacity-100');
    expect(screen.getByRole('button', { name: /主站群/ })).not.toHaveClass('pr-[88px]');

    // 展开时常显，且标题同步让出 88px——常显按钮不与群名称重叠。
    rerender(<GroupSidebar {...makeProps({ expandedGroupIds: { g1: true }, selectedGroupId: 'g1' })} />);
    const actionsExpanded = screen.getByRole('button', { name: '协作群操作' }).parentElement?.parentElement as HTMLElement;
    expect(actionsExpanded).toHaveClass('opacity-100');
    expect(screen.getByRole('button', { name: /主站群/ })).toHaveClass('pr-[88px]');

    rerender(
      <GroupSidebar
        {...makeProps({
          expandedGroupIds: {},
          selectedGroupId: null,
          sessionTabsByGroup: { g1: 'favorite' },
        })}
      />,
    );
    const actionsFavorite = screen.getByRole('button', { name: '协作群操作' }).parentElement?.parentElement as HTMLElement;
    expect(actionsFavorite).toHaveClass('opacity-100');
    expect(screen.getByRole('button', { name: /主站群/ })).toHaveClass('pr-[88px]');
  });

  it('协作群列表头部搜索行保持吸顶(视图切换收口到 App Shell)', () => {
    render(<GroupSidebar {...makeProps()} />);

    // v2：搜索行（input → relative wrapper → GroupSidebarSearchRow 行 → sticky 容器）。
    const toolRow = screen.getByRole('textbox', { name: '搜索协作群' }).parentElement?.parentElement
      ?.parentElement as HTMLElement;
    expect(toolRow).toHaveClass('sticky', 'top-0', 'z-20', 'bg-muted/20');
    expect(toolRow?.parentElement).toHaveClass('app-scrollbar', 'overflow-y-auto');
  });

  it('v2：shell 边缘 chevron 收起侧栏，竖轨群头像直达（选中+反展开），顶部 + 发起协作', () => {
    const onSelectGroup = jest.fn();
    const onCreateGroup = jest.fn();
    render(<GroupSidebar {...makeProps({ onSelectGroup, onCreateGroup })} />);
    const aside = screen.getByRole('complementary', { name: '协作群列表' });
    expect(aside).toHaveAttribute('data-collapsed', 'false');
    // 搜索行「+」发起协作不承担收起；收起统一走 shell 边缘 chevron（与 restricted 用例同通道）。
    fireEvent.click(screen.getByRole('button', { name: '收起对话协作左栏' }));
    expect(aside).toHaveAttribute('data-collapsed', 'true');
    // 竖轨：+ 发起协作入口存在（展开态搜索行内容已随 aria-hidden 移出可达树）；群头像直达 = 选中该群并展开侧栏。
    expect(screen.getByRole('button', { name: '发起协作' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开协作群 主站群' }));
    expect(onSelectGroup).toHaveBeenCalledWith('g1');
    expect(aside).toHaveAttribute('data-collapsed', 'false');
  });

  it('v2 竖轨：restricted 单群视图无「+」发起协作入口，底部按钮仅展开', () => {
    const onCreateGroup = jest.fn();
    render(<GroupSidebar {...makeProps({ restricted: true, onCreateGroup })} />);
    // restricted 无搜索行（其中「+」发起协作也不渲染），经 shell 边缘 chevron 收起。
    fireEvent.click(screen.getByRole('button', { name: '收起对话协作左栏' }));
    expect(screen.queryByRole('button', { name: '发起协作' })).not.toBeInTheDocument();
    // 竖轨底部按钮仅展开，不携带跨群入口。
    fireEvent.click(screen.getByRole('button', { name: '展开协作群列表' }));
    expect(screen.getByRole('complementary', { name: '协作群列表' })).toHaveAttribute('data-collapsed', 'false');
  });
});

it('restricted group list retains group sessions without cross-group search, filters or create entry', () => {
  render(<GroupSidebar {...makeProps({ restricted: true })} />);
  expect(screen.getByText('主站群')).toBeInTheDocument();
  expect(screen.getByText('会话一')).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: '搜索协作群' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '筛选协作群' })).not.toBeInTheDocument();
  expect(screen.queryByText('协作群')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '发起协作' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '切换工作身份' })).not.toBeInTheDocument();
});
