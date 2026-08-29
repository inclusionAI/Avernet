/** @jest-environment jsdom */
import type { GroupView } from '@/domain/collaboration';
import { GroupManageDrawer, type GroupManageDrawerProps } from '@/pages/Workspace/components/Modals/GroupManageDrawer';
import type { PolicyResult } from '@/services/workspace/groupService';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const baseGroup: GroupView = {
  groupId: 'g1',
  name: '我的群',
  kind: 'free_chat',
  status: 'active',
  participants: [],
  participantCount: 0,
  sessions: [],
  lastMessageAt: 0,
  createdAt: 0,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

const allowed: PolicyResult = { allowed: true };

// 强类型 mock 工厂：onUpdate 返回 void | Promise<DomainResult<GroupView>>，
// jest.fn() 默认推断为 unknown，与联合返回类型不兼容，故显式断言为 prop 类型。
const makeUpdate = (): GroupManageDrawerProps['onUpdate'] =>
  jest.fn((): void => undefined) as unknown as GroupManageDrawerProps['onUpdate'];

it('renders visibility Segmented with private/public options', () => {
  render(
    <GroupManageDrawer
      group={baseGroup}
      canManage={allowed}
      onClose={jest.fn()}
      onUpdate={makeUpdate()}
      onDissolve={jest.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: '私密' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '公开' })).toBeInTheDocument();
});

it('renders delivery_policy control for free_chat kind', () => {
  render(
    <GroupManageDrawer
      group={baseGroup}
      canManage={allowed}
      onClose={jest.fn()}
      onUpdate={makeUpdate()}
      onDissolve={jest.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: '自动回复' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '关闭自动回复' })).toBeInTheDocument();
});

it('dissolve button opens ConfirmDialog with impact text; confirm calls onDissolve', () => {
  const onDissolve = jest.fn();
  render(
    <GroupManageDrawer
      group={baseGroup}
      canManage={allowed}
      onClose={jest.fn()}
      onUpdate={makeUpdate()}
      onDissolve={onDissolve}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: /解散群/ }));
  expect(screen.getByText(/解散后将无法恢复/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '确认解散' }));
  expect(onDissolve).toHaveBeenCalledTimes(1);
});

it('when !canManage.allowed: controls disabled and disabledReason shown', () => {
  const deny: PolicyResult = { allowed: false, disabledReason: '仅管理员可管理' };
  render(
    <GroupManageDrawer
      group={baseGroup}
      canManage={deny}
      onClose={jest.fn()}
      onUpdate={makeUpdate()}
      onDissolve={jest.fn()}
    />,
  );
  expect(screen.getByText('仅管理员可管理')).toBeInTheDocument();
  const privateBtn = screen.getByRole('button', { name: '私密' });
  const publicBtn = screen.getByRole('button', { name: '公开' });
  expect(privateBtn).toBeDisabled();
  expect(publicBtn).toBeDisabled();
  expect(screen.getByRole('button', { name: /解散群/ })).toBeDisabled();
});

it('onUpdate called when visibility changes to public', () => {
  const onUpdate = makeUpdate();
  render(
    <GroupManageDrawer
      group={baseGroup}
      canManage={allowed}
      onClose={jest.fn()}
      onUpdate={onUpdate}
      onDissolve={jest.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: '公开' }));
  expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ visibility: 'public' }));
});
