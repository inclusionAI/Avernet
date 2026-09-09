/** @jest-environment jsdom */
import { GroupMembersModal } from '@/components/CollaborationSquare/GroupMembersModal';
import type { PublicGroup, PublicGroupMember } from '@/domain/collaborationSquare/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

function makeGroup(typeLabel: string): PublicGroup {
  return {
    id: 'g1',
    name: '产品共创群',
    ownerBotName: '群主助手',
    ownerUserName: '示例用户',
    typeLabel,
    memberCount: 3,
    goal: '推进产品共创',
    memberListVisibility: 'visible' as const,
    canCreateSession: true,
  } as PublicGroup;
}

function member(overrides: Partial<PublicGroupMember> & { id: string }): PublicGroupMember {
  return { displayName: '成员', type: 'bot', role: 'member', ...overrides } as PublicGroupMember;
}

function renderMembers(members: PublicGroupMember[], typeLabel = '自由聊天') {
  return render(
    <GroupMembersModal open group={makeGroup(typeLabel)} members={members} loading={false} onClose={jest.fn()} />,
  );
}

describe('GroupMembersModal（成员卡片对齐「对话协作」成员管理 card 样式）', () => {
  it('成员卡片：首字母圆标头像 + 名称 + 第二行类型/角色徽章（无移除等操作按钮）', () => {
    renderMembers([member({ id: 'b1', displayName: '驱动Bot', type: 'bot', role: 'driver' })]);
    // bot 头像：主色软底圆标（与 MemberList 一致）。
    expect(screen.getByText('驱')).toHaveClass('bg-primary/10', 'text-primary', 'rounded-full');
    // 类型徽章默认蓝；自由聊天群 driver → 「群主」紫色。
    expect(screen.getByText('Bot')).toHaveClass('bg-primary/10', 'text-primary');
    expect(screen.getByText('群主')).toHaveClass('bg-purple/10', 'text-purple');
    // 无移除/管理按钮。
    expect(screen.queryByRole('button', { name: /移除/ })).toBeNull();
  });

  it('自由聊天群：driver→群主，其余角色（member/manager/未知值）一律「成员」默认蓝', () => {
    renderMembers([
      member({ id: 'u1', displayName: '示例用户', type: 'human', role: 'member' }),
      member({ id: 'b2', displayName: '管理Bot', role: 'manager' }),
      member({ id: 'b3', displayName: '执行Bot', role: 'worker' }),
    ]);
    expect(screen.getByText('示')).toHaveClass('bg-brand/15', 'text-brand');
    expect(screen.getByText('用户')).toHaveClass('bg-primary/10', 'text-primary');
    // 非 driver 全部展示「成员」。
    expect(screen.getAllByText('成员').length).toBe(3);
    expect(screen.getAllByText('成员')[0]).toHaveClass('bg-primary/10', 'text-primary');
    expect(screen.queryByText('主节点')).toBeNull();
    expect(screen.queryByText('从节点')).toBeNull();
  });

  it('任务协作群：manager→主节点（紫）、worker→从节点（默认蓝），其余一律「成员」', () => {
    renderMembers(
      [
        member({ id: 'b1', displayName: '主节点Bot', role: 'manager' }),
        member({ id: 'b2', displayName: '从节点Bot', role: 'worker' }),
        member({ id: 'b3', displayName: '驱动Bot', role: 'driver' }),
        member({ id: 'u1', displayName: '示例用户', type: 'human', role: 'member' }),
      ],
      '任务协作',
    );
    expect(screen.getByText('主节点')).toHaveClass('bg-purple/10', 'text-purple');
    expect(screen.getByText('从节点')).toHaveClass('bg-primary/10', 'text-primary');
    // driver 与 member 在任务协作群都不是特殊角色 → 「成员」。
    expect(screen.getAllByText('成员').length).toBe(2);
  });

  it('后端返回中文角色值等未知 role 也归一为「成员」', () => {
    renderMembers([member({ id: 'u2', displayName: '示例用户', type: 'human', role: '参与者' })]);
    expect(screen.getByText('成员')).toBeInTheDocument();
    expect(screen.queryByText('参与者')).toBeNull();
    expect(screen.getByText('用户')).toBeInTheDocument();
    expect(screen.queryByText('Human')).toBeNull();
  });
});
