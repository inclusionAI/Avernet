/** @jest-environment jsdom */
import type { ParticipantView } from '@/domain/collaboration';
import { MemberList } from '@/pages/Workspace/components/ManagePanel/MemberList';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const baseParticipant: ParticipantView = {
  actorId: 'bot-driver',
  kind: 'bot',
  name: '驱动 Bot',
  role: 'driver',
  mode: 'auto',
};

function renderMembers(
  participants: ParticipantView[],
  props: {
    groupKind?: 'free_chat' | 'task_master_slave' | 'task_dag';
    showMode?: boolean;
    badgePolicy?: 'full' | 'session';
  } = {},
) {
  return render(
    <MemberList
      participants={participants}
      canManage={false}
      onAddMany={jest.fn()}
      onRemove={jest.fn()}
      groupKind={props.groupKind ?? 'free_chat'}
      showMode={props.showMode}
      badgePolicy={props.badgePolicy}
    />,
  );
}

function memberRow(actorId: string) {
  return screen.getByTestId(`member-${actorId}`);
}

describe('MemberList', () => {
  it('uses body-sized member names below the card heading hierarchy', () => {
    renderMembers([baseParticipant]);

    expect(within(memberRow('bot-driver')).getByText('驱动 Bot')).toHaveClass('text-xs', 'font-medium');
  });

  it('自由聊天群仅在第二行展示成员类型与群主标签，不展示成员模式', () => {
    renderMembers(
      [
        { ...baseParticipant, actorId: 'bot-driver', mode: 'muted' },
        {
          ...baseParticipant,
          actorId: 'human-member',
          kind: 'human',
          name: '普通用户',
          role: 'member',
          mode: 'present',
        },
      ],
      { groupKind: 'free_chat' },
    );

    expect(memberRow('bot-driver').textContent).toContain('驱动 Bot');
    expect(memberRow('bot-driver').textContent).toContain('Bot');
    expect(memberRow('bot-driver').textContent).toContain('群主');
    // 标签分类型着色：成员类型默认蓝（primary），群主/主节点紫（purple），管理面板内字号统一为 10px。
    expect(screen.getByText('Bot')).toHaveClass('text-[11px]', 'bg-primary/10', 'text-primary');
    expect(screen.getByText('群主')).toHaveClass('text-[11px]', 'bg-purple/10', 'text-purple');
    // bot 图标对齐 Bot 工坊信息列：主色软底圆形首字母（不再黑底 bg-foreground）。
    expect(screen.getByText('驱')).toHaveClass('bg-primary/10', 'text-primary', 'font-medium');
    expect(memberRow('bot-driver').textContent).not.toContain('禁言');
    expect(memberRow('human-member').textContent).toContain('用户');
    // 自由聊天群：非 driver 一律展示「成员」（默认蓝）。
    expect(memberRow('human-member').textContent).toContain('成员');
    expect(screen.getByText('成员')).toHaveClass('bg-primary/10', 'text-primary');
  });

  it('可管理时移除入口为常驻图标按钮（UserMinus，悬停变红），点击走二次确认', () => {
    render(
      <MemberList
        participants={[baseParticipant]}
        canManage
        onAddMany={jest.fn()}
        onRemove={jest.fn()}
        groupKind="free_chat"
      />,
    );
    // 验收微调：移除入口改为图标按钮（与 MembersPanel 的 UserMinus 先例同构）；
    // 常驻可见、中性灰悬停变红；可访问名称带成员名区分多行；点击走二次确认。
    const removeButton = screen.getByRole('button', { name: '移除成员 驱动 Bot' });
    expect(removeButton.textContent).toBe('');
    expect(removeButton).not.toHaveClass('opacity-0');
    expect(removeButton).toHaveClass('text-muted-foreground', 'hover:text-destructive');
    // 点击移除走二次确认，不直接触发 onRemove
    fireEvent.click(removeButton);
    expect(screen.getByText('确认移除')).toBeInTheDocument();
  });

  it('任务协作群按角色展示主/从节点标签（不区分 bot/human）', () => {
    renderMembers(
      [
        { ...baseParticipant, actorId: 'manager', name: '主节点 Bot', role: 'manager' },
        { ...baseParticipant, actorId: 'worker', name: '从节点 Bot', role: 'worker' },
        {
          ...baseParticipant,
          actorId: 'human-manager',
          kind: 'human',
          name: '用户管理员',
          role: 'manager',
        },
      ],
      { groupKind: 'task_master_slave' },
    );

    expect(memberRow('manager').textContent).toContain('主节点');
    expect(memberRow('worker').textContent).toContain('从节点');
    // 角色映射不区分 bot/human：human 的 manager 同样展示「主节点」。
    expect(memberRow('human-manager').textContent).toContain('主节点');
    // 主节点紫；从节点走默认蓝。
    expect(screen.getAllByText('主节点')[0]).toHaveClass('bg-purple/10', 'text-purple');
    expect(screen.getByText('从节点')).toHaveClass('bg-primary/10', 'text-primary');
  });

  it('自定义协作群：driver→群主，其余展示「成员」', () => {
    renderMembers(
      [
        { ...baseParticipant, actorId: 'driver', role: 'driver' },
        { ...baseParticipant, actorId: 'consultant', name: '顾问 Bot', role: 'member' },
      ],
      { groupKind: 'task_dag' },
    );

    expect(memberRow('driver').textContent).toContain('群主');
    expect(memberRow('consultant').textContent).not.toContain('群主');
    expect(memberRow('consultant').textContent).toContain('成员');
  });

  it('会话精简档：bot 展示类型与自动/禁言状态，人类不展示参与/旁观状态', () => {
    renderMembers(
      [
        { ...baseParticipant, actorId: 'bot-auto', name: '自由 Bot', mode: 'auto' },
        { ...baseParticipant, actorId: 'bot-muted', name: '禁言 Bot', mode: 'muted' },
        {
          ...baseParticipant,
          actorId: 'human-present',
          kind: 'human',
          name: '参与用户',
          role: 'member',
          mode: 'present',
        },
        {
          ...baseParticipant,
          actorId: 'human-absent',
          kind: 'human',
          name: '旁观用户',
          role: 'member',
          mode: 'absent',
        },
      ],
      { groupKind: 'task_master_slave', showMode: true, badgePolicy: 'session' },
    );

    expect(memberRow('bot-auto').textContent).toContain('自动');
    expect(memberRow('bot-muted').textContent).toContain('禁言');
    expect(memberRow('bot-auto').textContent).toContain('Bot');
    // 会话精简档：人类参与状态不在列表重复展示（会话输入区上方已标识）。
    // 用 queryByText 精确匹配徽标元素，避免成员名（参与用户/旁观用户）包含同词导致误伤。
    expect(screen.queryByText('参与')).not.toBeInTheDocument();
    expect(screen.queryByText('旁观')).not.toBeInTheDocument();
    expect(memberRow('human-present').textContent).toContain('用户');
    // 状态色：自动绿（success），禁言灰（neutral）。
    expect(screen.getByText('自动')).toHaveClass('bg-success/10', 'text-success');
    expect(screen.getByText('禁言')).toHaveClass('bg-muted', 'text-muted-foreground');
  });

  it('会话精简档：角色徽标仅群主/主节点标识，成员/从节点不再标识', () => {
    const { unmount } = renderMembers(
      [
        { ...baseParticipant, actorId: 'bot-driver', role: 'driver' },
        { ...baseParticipant, actorId: 'bot-member', role: 'member' },
      ],
      { groupKind: 'free_chat', badgePolicy: 'session' },
    );
    expect(memberRow('bot-driver').textContent).toContain('群主');
    expect(memberRow('bot-member').textContent).toContain('Bot');
    expect(memberRow('bot-member').textContent).not.toContain('成员');
    unmount();

    renderMembers(
      [
        { ...baseParticipant, actorId: 'bot-manager', role: 'manager' },
        { ...baseParticipant, actorId: 'bot-worker', role: 'worker' },
      ],
      { groupKind: 'task_master_slave', badgePolicy: 'session' },
    );
    expect(memberRow('bot-manager').textContent).toContain('主节点');
    expect(memberRow('bot-worker').textContent).not.toContain('从节点');
    expect(memberRow('bot-worker').textContent).not.toContain('成员');
  });

  it('会话精简档：human 即使回显 messageViewScope 也不展示视角标签', () => {
    renderMembers(
      [
        {
          ...baseParticipant,
          actorId: 'human-scoped',
          kind: 'human',
          name: '带视角用户',
          role: 'member',
          mode: 'present',
          messageViewScope: 'participant',
        },
      ],
      { groupKind: 'free_chat', showMode: true, badgePolicy: 'session' },
    );

    expect(memberRow('human-scoped').textContent).toContain('用户');
    expect(memberRow('human-scoped').textContent).not.toContain('参与者视角');
    expect(memberRow('human-scoped').textContent).not.toContain('参与');
  });

  it('human 成员回显 messageViewScope 时展示对应视角标签', () => {
    renderMembers(
      [
        {
          ...baseParticipant,
          actorId: 'human-participant',
          kind: 'human',
          name: '参与者用户',
          role: 'member',
          mode: 'present',
          messageViewScope: 'participant',
        },
        {
          ...baseParticipant,
          actorId: 'human-full',
          kind: 'human',
          name: '完整视角用户',
          role: 'member',
          mode: 'present',
          messageViewScope: 'full',
        },
      ],
      { groupKind: 'task_master_slave', showMode: true },
    );

    expect(memberRow('human-participant').textContent).toContain('参与者视角');
    expect(memberRow('human-full').textContent).toContain('完整视角');
    // 视角标签色：full 主色蓝（primary），participant 灰（neutral）。
    expect(screen.getByText('完整视角')).toHaveClass('text-[11px]', 'bg-primary/10', 'text-primary');
    expect(screen.getByText('参与者视角')).toHaveClass('text-[11px]', 'bg-muted', 'text-muted-foreground');
  });

  it('human 成员无 messageViewScope 回显时不展示视角标签', () => {
    renderMembers(
      [
        {
          ...baseParticipant,
          actorId: 'human-plain',
          kind: 'human',
          name: '普通用户',
          role: 'member',
          mode: 'present',
        },
      ],
      { groupKind: 'task_master_slave', showMode: true },
    );

    expect(memberRow('human-plain').textContent).not.toContain('完整视角');
    expect(memberRow('human-plain').textContent).not.toContain('参与者视角');
  });

  it('bot 成员即使误带 messageViewScope 也不展示视角标签（按 kind 门控）', () => {
    renderMembers(
      [
        {
          ...baseParticipant,
          actorId: 'bot-scope',
          name: '带视角 Bot',
          mode: 'auto',
          messageViewScope: 'participant',
        },
      ],
      { groupKind: 'task_master_slave', showMode: true },
    );

    expect(memberRow('bot-scope').textContent).not.toContain('参与者视角');
    expect(memberRow('bot-scope').textContent).not.toContain('完整视角');
  });

  // ===== 窄面板标签折叠（验收微调，容器查询作用于成员行）=====
  // jsdom 不计算容器查询，断言以类名锁定折叠档位：
  // 行内容宽 <400px 收起可选标签（普通角色/状态/视角），<300px 连管理角色（群主/主节点）也收起。

  it('全量档：类型标签常驻，可选标签一级收起、群主二级收起，渲染 ... 展开入口', () => {
    renderMembers(
      [
        { ...baseParticipant, actorId: 'bot-driver', role: 'driver', mode: 'auto' },
        {
          ...baseParticipant,
          actorId: 'human-member',
          kind: 'human',
          name: '普通用户',
          role: 'member',
          mode: 'present',
          messageViewScope: 'participant',
        },
      ],
      { groupKind: 'free_chat', showMode: true },
    );

    // 类型标签（Bot/用户）不参与收起。
    expect(screen.getByText('Bot')).not.toHaveClass('@max-w-[400px]:hidden');
    expect(screen.getByText('用户')).not.toHaveClass('@max-w-[300px]:hidden');
    // 管理角色（群主）保留至二级紧凑（<300px）才收起。
    expect(screen.getByText('群主')).toHaveClass('@max-w-[300px]:hidden');
    // 可选标签（普通角色/状态/视角）一级紧凑（<400px）即收起。
    expect(screen.getByText('成员')).toHaveClass('@max-w-[400px]:hidden');
    expect(screen.getByText('自动')).toHaveClass('@max-w-[400px]:hidden');
    expect(screen.getByText('参与')).toHaveClass('@max-w-[400px]:hidden');
    expect(screen.getByText('参与者视角')).toHaveClass('@max-w-[400px]:hidden');
    // 两行均有可收起标签 → 各渲染一个 ... 展开入口；默认隐藏，紧凑档恢复显示。
    const moreButtons = screen.getAllByRole('button', { name: '更多成员标签' });
    expect(moreButtons).toHaveLength(2);
    expect(moreButtons[0]).toHaveClass('hidden', '@max-w-[400px]:inline-flex');
  });

  it('会话精简档：bot 状态标签一级收起；无可收起标签的普通用户行不渲染 ... 入口', () => {
    renderMembers(
      [
        { ...baseParticipant, actorId: 'bot-auto', role: 'member', mode: 'auto' },
        {
          ...baseParticipant,
          actorId: 'human-plain',
          kind: 'human',
          name: '普通用户',
          role: 'member',
          mode: 'present',
        },
      ],
      { groupKind: 'free_chat', showMode: true, badgePolicy: 'session' },
    );

    expect(screen.getByText('自动')).toHaveClass('@max-w-[400px]:hidden');
    expect(screen.getByText('Bot')).not.toHaveClass('@max-w-[400px]:hidden');
    // bot 行有可收起的状态标签 → 有 ... 入口；human 行仅类型标签 → 不渲染。
    expect(screen.getAllByRole('button', { name: '更多成员标签' })).toHaveLength(1);
  });

  it('... 入口 hover/聚焦后展开显示被收起的标签', async () => {
    renderMembers([{ ...baseParticipant, actorId: 'bot-driver', role: 'driver', mode: 'auto' }], {
      groupKind: 'free_chat',
      showMode: true,
    });

    const moreButton = screen.getByRole('button', { name: '更多成员标签' });
    // jsdom 不完整支持 pointer 事件（hover 路径无法模拟），改用同一 Tooltip 交互通道的 focus 触发展开；
    // hover 行为由 Radix Tooltip 组件保证，目标页面复验覆盖。
    fireEvent.focus(moreButton);
    // Tooltip 展开后「群主/自动」在行内与浮层各出现一次。
    await waitFor(() => {
      expect(screen.getAllByText('群主')).toHaveLength(2);
      expect(screen.getAllByText('自动')).toHaveLength(2);
    });
  });
});
