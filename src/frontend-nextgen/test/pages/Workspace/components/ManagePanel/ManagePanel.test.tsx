/** @jest-environment jsdom */
import type { GroupView, IdentityView, SessionView } from '@/domain/collaboration';
import {
  GroupManagePanel,
  type GroupManagePanelProps,
} from '@/pages/Workspace/components/ManagePanel/GroupManagePanel';
import {
  SessionManagePanel,
  type SessionManagePanelProps,
} from '@/pages/Workspace/components/ManagePanel/SessionManagePanel';
import type { PolicyResult } from '@/services/workspace/groupService';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const allowed: PolicyResult = { allowed: true };

const identity: IdentityView = {
  id: 'me',
  kind: 'user',
  displayName: '我',
  online: true,
};

const group: GroupView = {
  groupId: 'g1',
  name: '我的群',
  kind: 'free_chat',
  status: 'active',
  participants: [{ actorId: 'me', kind: 'human', name: '我', role: 'owner', mode: 'present' }],
  participantCount: 1,
  sessions: [],
  lastMessageAt: 0,
  createdAt: 0,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

// 会话删除权限只认 driver/manager（SessionManagePanel 自行按 participants 判定，不看 canManage prop；
// 与 groupService.canManageGroup 的 driver/manager 判定一致）。这里用 driver 覆盖「可管理」路径。
const session: SessionView = {
  sessionId: 's1',
  groupId: 'g1',
  title: '会话',
  kind: 'chat',
  status: 'running',
  participants: [{ actorId: 'me', kind: 'human', name: '我', role: 'driver', mode: 'present' }],
  lastMessageAt: 0,
  createdAt: 0,
  favorite: false,
};

const groupProps = (): GroupManagePanelProps => ({
  group,
  canManage: allowed,
  activeIdentity: identity,
  candidates: [identity],
  onClose: jest.fn(),
  onUpdate: jest.fn(async () => null),
  onDissolve: jest.fn(),
  onLeaveGroup: jest.fn(async () => true),
  onAddMember: jest.fn(async () => true),
  onRemoveMember: jest.fn(async () => true),
  onShare: jest.fn(async () => ({ ok: true as const, data: { invitationUrl: 'http://example.com/g1' } })),
  onSaveDingTalk: jest.fn(async () => false),
  onToggleDingTalkActive: jest.fn(async () => true),
  onDeleteDingTalk: jest.fn(async () => true),
  dingTalkBinding: null,
  dingTalkLoading: false,
  advancedConfigEnabled: true,
});

it('group panel exposes base info tabs and member management', () => {
  render(<GroupManagePanel {...groupProps()} />);
  expect(screen.getByRole('tab', { name: '基础信息' })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: '高级配置' })).toBeInTheDocument();
  expect(screen.getByText('群成员管理')).toBeInTheDocument();
  expect(screen.getByText('分享协作群')).toBeInTheDocument();
  expect(screen.getByText('用户可以通过链接加入群组')).toBeInTheDocument();
  expect(screen.queryByText(/Human/)).not.toBeInTheDocument();
  expect(screen.getByText('删除协作群')).toBeInTheDocument();
});

it('group panel advanced tab renders dingtalk binding form', () => {
  render(<GroupManagePanel {...groupProps()} />);
  expect(screen.getByText('高级配置')).toBeInTheDocument();
});

it('group panel hides advanced tab when advanced config is disabled', () => {
  render(<GroupManagePanel {...groupProps()} advancedConfigEnabled={false} />);
  expect(screen.queryByRole('tab', { name: '高级配置' })).not.toBeInTheDocument();
});

// 非管理者：仅当当前身份是群直属成员（在 participants 名单中）才展示「退出协作群」；
// 非成员（仅可查看的访客）不展示退出按钮，避免误以为自己是群成员。
it('group panel shows 退出协作群 only for direct member when not manager', () => {
  const denied: PolicyResult = { allowed: false, disabledReason: '无权限' };
  const memberProps: GroupManagePanelProps = {
    ...groupProps(),
    canManage: denied,
    group: { ...group, participants: [{ actorId: 'me', kind: 'human', name: '我', role: 'member', mode: 'present' }] },
  };
  render(<GroupManagePanel {...memberProps} />);
  expect(screen.getByText('退出协作群')).toBeInTheDocument();
  expect(screen.queryByText('删除协作群')).not.toBeInTheDocument();
});

it('group panel hides 退出协作群 for non-member viewer', () => {
  const denied: PolicyResult = { allowed: false, disabledReason: '无权限' };
  const viewerProps: GroupManagePanelProps = {
    ...groupProps(),
    canManage: denied,
    group: {
      ...group,
      participants: [{ actorId: 'other', kind: 'human', name: '他人', role: 'owner', mode: 'present' }],
    },
  };
  render(<GroupManagePanel {...viewerProps} />);
  expect(screen.queryByText('退出协作群')).not.toBeInTheDocument();
  expect(screen.queryByText('删除协作群')).not.toBeInTheDocument();
});

const sessionProps = (): SessionManagePanelProps => ({
  session,
  groupName: group.name,
  canManage: allowed,
  activeIdentity: identity,
  candidates: [identity],
  onClose: jest.fn(),
  onRename: jest.fn(async () => true),
  onDelete: jest.fn(async () => true),
  onLeaveSession: jest.fn(async () => true),
  onAddMember: jest.fn(async () => true),
  onRemoveMember: jest.fn(async () => true),
  onShare: jest.fn(async () => ({ ok: true as const, data: { invitationUrl: 'http://example.com/s1' } })),
});

it('session panel renders basic info, members, share and delete', () => {
  render(<SessionManagePanel {...sessionProps()} />);
  expect(screen.getByText('会话管理')).toBeInTheDocument();
  expect(screen.getByText('会话成员管理')).toBeInTheDocument();
  expect(screen.getByText('分享会话')).toBeInTheDocument();
  expect(screen.getByText('删除会话')).toBeInTheDocument();
  expect(screen.queryByText('高级配置')).not.toBeInTheDocument();
});

// 锁定「非 driver/manager 只读」这条分支：即使传入 canManage.allowed=true（调用方传的是群级策略），
// 会话面板仍按 participants 角色收敛为可查看、不给删除入口。若将来要放开（如群 owner 可删会话），
// 应是显式改动并同步改这条用例。
it('session panel hides delete for non driver/manager even when canManage is allowed', () => {
  const viewerSession: SessionView = {
    ...session,
    participants: [{ actorId: 'me', kind: 'human', name: '我', role: 'member', mode: 'present' }],
  };
  render(<SessionManagePanel {...sessionProps()} session={viewerSession} />);
  expect(screen.getByText('可查看')).toBeInTheDocument();
  expect(screen.queryByText('删除会话')).not.toBeInTheDocument();
});
