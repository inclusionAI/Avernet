/** @jest-environment jsdom */
import type { ParticipantView } from '@/domain/collaboration';
import { MemberList } from '@/pages/Workspace/components/ManagePanel/MemberList';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen, within } from '@testing-library/react';

const baseParticipant: ParticipantView = {
  actorId: 'bot-driver',
  kind: 'bot',
  name: '驱动 Bot',
  role: 'driver',
  mode: 'auto',
};

function renderMembers(
  participants: ParticipantView[],
  props: { groupKind?: 'free_chat' | 'task_master_slave' | 'task_dag'; showMode?: boolean } = {},
) {
  return render(
    <MemberList
      participants={participants}
      canManage={false}
      onAddMany={jest.fn()}
      onRemove={jest.fn()}
      groupKind={props.groupKind ?? 'free_chat'}
      showMode={props.showMode}
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
    expect(screen.getByText('Bot')).toHaveClass('text-[10px]', 'bg-primary/10', 'text-primary');
    expect(screen.getByText('群主')).toHaveClass('text-[10px]', 'bg-purple/10', 'text-purple');
    // bot 图标对齐 Bot 工坊信息列：主色软底圆形首字母（不再黑底 bg-foreground）。
    expect(screen.getByText('驱')).toHaveClass('bg-primary/10', 'text-primary', 'font-semibold');
    expect(memberRow('bot-driver').textContent).not.toContain('禁言');
    expect(memberRow('human-member').textContent).toContain('用户');
    // 自由聊天群：非 driver 一律展示「成员」（默认蓝）。
    expect(memberRow('human-member').textContent).toContain('成员');
    expect(screen.getByText('成员')).toHaveClass('bg-primary/10', 'text-primary');
  });

  it('可管理时移除入口为红色垃圾桶图标按钮（无文字）', () => {
    render(
      <MemberList
        participants={[baseParticipant]}
        canManage
        onAddMany={jest.fn()}
        onRemove={jest.fn()}
        groupKind="free_chat"
      />,
    );
    const removeButton = screen.getByRole('button', { name: '移除成员' });
    expect(removeButton).toHaveClass('text-destructive');
    expect(removeButton.textContent).toBe('');
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

  it('会话成员第二行按成员类型展示模式标签', () => {
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
      { groupKind: 'task_master_slave', showMode: true },
    );

    expect(memberRow('bot-auto').textContent).toContain('自动');
    expect(memberRow('bot-muted').textContent).toContain('禁言');
    expect(memberRow('human-present').textContent).toContain('参与');
    expect(memberRow('human-absent').textContent).toContain('旁观');
    // 状态色：自动/参与绿（success），禁言/旁观灰（neutral）。
    expect(screen.getByText('自动')).toHaveClass('bg-success/10', 'text-success');
    expect(screen.getByText('参与')).toHaveClass('bg-success/10', 'text-success');
    expect(screen.getByText('禁言')).toHaveClass('bg-muted', 'text-muted-foreground');
    expect(screen.getByText('旁观')).toHaveClass('bg-muted', 'text-muted-foreground');
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
    expect(screen.getByText('完整视角')).toHaveClass('text-[10px]', 'bg-primary/10', 'text-primary');
    expect(screen.getByText('参与者视角')).toHaveClass('text-[10px]', 'bg-muted', 'text-muted-foreground');
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
});
