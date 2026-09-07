/** @jest-environment jsdom */
import type { ParticipantView } from '@/domain/collaboration';
import { MemberList } from '@/pages/Workspace/components/ManagePanel/MemberList';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

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
    expect(screen.getByText('Bot')).toHaveClass('text-[10px]');
    expect(screen.getByText('群主')).toHaveClass('text-[10px]');
    expect(memberRow('bot-driver').textContent).not.toContain('禁言');
    expect(memberRow('human-member').textContent).toContain('用户');
    expect(memberRow('human-member').textContent).not.toContain('成员');
  });

  it('任务协作群展示主从节点标签，human 成员不展示角色', () => {
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
    expect(memberRow('human-manager').textContent).not.toContain('主节点');
  });

  it('自定义协作群仅展示群主标签', () => {
    renderMembers(
      [
        { ...baseParticipant, actorId: 'driver', role: 'driver' },
        { ...baseParticipant, actorId: 'consultant', name: '顾问 Bot', role: 'member' },
      ],
      { groupKind: 'task_dag' },
    );

    expect(memberRow('driver').textContent).toContain('群主');
    expect(memberRow('consultant').textContent).not.toContain('群主');
    expect(memberRow('consultant').textContent).not.toContain('成员');
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

    expect(memberRow('bot-auto').textContent).toContain('自由');
    expect(memberRow('bot-muted').textContent).toContain('禁言');
    expect(memberRow('human-present').textContent).toContain('参与');
    expect(memberRow('human-absent').textContent).toContain('旁观');
  });
});
