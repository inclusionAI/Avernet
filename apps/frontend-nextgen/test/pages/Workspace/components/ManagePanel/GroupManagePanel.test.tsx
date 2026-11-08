/** @jest-environment jsdom */
// 基础信息卡新增「@Human 通知来源」下拉（值来自 GET 群详情 human_mention_notify_mode，
// 写入走既有 onUpdate 补丁链路）。本文件聚焦该字段的受控行为；群/成员等其余面板断言见 ManagePanel.test.tsx。
import type { GroupUpdatePatch, GroupView, IdentityView } from '@/domain/collaboration';
import { GroupManagePanel, type GroupManagePanelProps } from '@/pages/Workspace/components/ManagePanel/GroupManagePanel';
import { ResizableWorkspaceSidebar } from '@/pages/Workspace/components/ResizableWorkspaceSidebar';
import type { PolicyResult } from '@/services/workspace/groupService';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// jsdom 未实现 Pointer Capture，补齐 Radix Select 测试所需的最小 API。
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => undefined;
Element.prototype.releasePointerCapture ??= () => undefined;
Element.prototype.scrollIntoView ??= () => undefined;

const allowed: PolicyResult = { allowed: true };

const identity: IdentityView = { id: 'me', kind: 'user', displayName: '我', online: true };

const baseGroup: GroupView = {
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

const panelProps = (overrides: Partial<GroupManagePanelProps> = {}): GroupManagePanelProps => ({
  group: baseGroup,
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
  ...overrides,
});

const trigger = () => screen.getByRole('combobox', { name: '@Human 通知来源' });

it.each([
  ['1 天', 86400],
  ['7 天', 604800],
  ['30 天', 2592000],
])('分享协作群卡片定位在操作按钮，选择 %s 后才生成链接', async (expiryLabel, seconds) => {
  const user = userEvent.setup();
  const props = panelProps();
  render(
    <ResizableWorkspaceSidebar ariaLabel="群管理面板" side="right" onDismiss={props.onClose}>
      <GroupManagePanel {...props} />
    </ResizableWorkspaceSidebar>,
  );
  const shareButton = screen.getByRole('button', { name: /分享协作群/ });
  const measureAnchor = jest.spyOn(shareButton, 'getBoundingClientRect');

  await user.click(shareButton);
  expect(screen.getByRole('button', { name: '生成链接' })).toBeVisible();
  // 没有真实锚点时 Radix 无法定位，内容会一直停在屏幕外的初始位置。
  await waitFor(() => expect(measureAnchor).toHaveBeenCalled());
  expect(props.onShare).not.toHaveBeenCalled();

  await user.click(screen.getByRole('button', { name: expiryLabel }));
  await user.click(screen.getByRole('button', { name: '生成链接' }));
  await waitFor(() => expect(props.onShare).toHaveBeenCalledWith(seconds));
  expect(props.onShare).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText('邀请链接')).toHaveTextContent('http://example.com/g1');
  expect(props.onClose).not.toHaveBeenCalled();
});

it('基础信息卡渲染「@Human 通知来源」标签与三个选项，当前值受控自群详情', async () => {
  const user = userEvent.setup();
  render(
    <GroupManagePanel {...panelProps()} group={{ ...baseGroup, humanMentionNotifyMode: 'driver_bot_only' }} />,
  );

  expect(screen.getByText('@Human 通知来源')).toBeInTheDocument();
  expect(trigger()).toHaveTextContent('仅群主 Bot');

  await user.click(trigger());
  expect(screen.getByRole('option', { name: '所有成员' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: '仅群主 Bot' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: '不通知' })).toBeInTheDocument();
});

it('旧版后端详情缺省该字段时按服务端存量默认（all）展示「所有成员」', () => {
  render(<GroupManagePanel {...panelProps()} />);

  expect(trigger()).toHaveTextContent('所有成员');
});

it('选择「不通知」仅走 onUpdate({ humanMentionNotifyMode }) 补丁，不携带其他字段', async () => {
  const user = userEvent.setup();
  const onUpdate = jest.fn(async () => null);
  render(
    <GroupManagePanel
      {...panelProps({ onUpdate })}
      group={{ ...baseGroup, humanMentionNotifyMode: 'all' }}
    />,
  );

  await user.click(trigger());
  await user.click(screen.getByRole('option', { name: '不通知' }));

  await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
  const patch: GroupUpdatePatch = onUpdate.mock.calls[0][0];
  expect(patch).toEqual({ humanMentionNotifyMode: 'none' });
});

it('写入成功后父层以详情回读的新值受控重渲，下拉显示刷新后的值', async () => {
  const user = userEvent.setup();
  const nextGroup: GroupView = { ...baseGroup, humanMentionNotifyMode: 'none' };
  const onUpdate = jest.fn(async () => ({ ok: true as const, data: nextGroup }));
  const view = render(<GroupManagePanel {...panelProps({ onUpdate })} group={baseGroup} />);

  expect(trigger()).toHaveTextContent('所有成员');
  await user.click(trigger());
  await user.click(screen.getByRole('option', { name: '不通知' }));
  await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));

  // 受控父级语义：成功后仍以 group prop 为准；父层用回读的群详情 rerender。
  view.rerender(<GroupManagePanel {...panelProps({ onUpdate })} group={nextGroup} />);
  expect(trigger()).toHaveTextContent('不通知');
});

it('可查看（canManage=false）时下拉禁用，不触发写入', async () => {
  const user = userEvent.setup();
  const onUpdate = jest.fn(async () => null);
  render(
    <GroupManagePanel
      {...panelProps({
        onUpdate,
        canManage: { allowed: false, disabledReason: '仅群主/主节点可管理该协作群' },
      })}
      group={{ ...baseGroup, humanMentionNotifyMode: 'all' }}
    />,
  );

  expect(trigger()).toBeDisabled();
  await user.click(trigger()).catch(() => undefined);
  expect(onUpdate).not.toHaveBeenCalled();
});

it('写入失败：选择回滚为 group 原值且可重试（不落本地乐观值）', async () => {
  const user = userEvent.setup();
  const onUpdate = jest.fn(async () => ({
    ok: false as const,
    error: { code: 'GROUP_UPDATE_FAILED' as const, friendlyMessage: '更新协作群失败，请稍后重试。', canRetry: false },
  }));
  render(
    <GroupManagePanel
      {...panelProps({ onUpdate })}
      group={{ ...baseGroup, humanMentionNotifyMode: 'driver_bot_only' }}
    />,
  );

  await user.click(trigger());
  await user.click(screen.getByRole('option', { name: '不通知' }));
  await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));

  // 父层未刷新 group（失败不回写）→ 受控值回到原选项
  expect(trigger()).toHaveTextContent('仅群主 Bot');
  expect(trigger()).not.toBeDisabled();

  // 可重试：再次选择仍能发起写入
  await user.click(trigger());
  await user.click(screen.getByRole('option', { name: '不通知' }));
  await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(2));
});

it('写入进行中：下拉禁用防重复提交，结束后恢复可交互', async () => {
  const user = userEvent.setup();
  let resolveUpdate: (value: unknown) => void = () => undefined;
  const onUpdate = jest.fn(
    () =>
      new Promise((resolve) => {
        resolveUpdate = resolve;
      }),
  );
  render(
    <GroupManagePanel
      {...panelProps({ onUpdate })}
      group={{ ...baseGroup, humanMentionNotifyMode: 'all' }}
    />,
  );

  await user.click(trigger());
  await user.click(screen.getByRole('option', { name: '不通知' }));
  await waitFor(() => expect(trigger()).toBeDisabled());

  await act(async () => {
    resolveUpdate(null);
  });
  await waitFor(() => expect(trigger()).not.toBeDisabled());
  expect(onUpdate).toHaveBeenCalledTimes(1);
});

it('右侧面板内 @Human 下拉在 Portal 弹出层交互不触发外部点击收起；点面板外仍收起', async () => {
  const user = userEvent.setup();
  const onClose = jest.fn();
  const onUpdate = jest.fn(async () => null);
  render(
    <ResizableWorkspaceSidebar ariaLabel="群管理面板" side="right" onDismiss={onClose}>
      <GroupManagePanel
        {...panelProps({ onUpdate, onClose })}
        group={{ ...baseGroup, humanMentionNotifyMode: 'all' }}
      />
    </ResizableWorkspaceSidebar>,
  );

  await user.click(trigger());
  // 选项渲染在 Portal（role=listbox / popper 包裹层）——点击不得被外部点击 hook 误判收起。
  await user.click(screen.getByRole('option', { name: '不通知' }));
  await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
  expect(onClose).not.toHaveBeenCalled();

  // 关闭下拉后点击面板外仍照常收起。
  fireEvent.pointerDown(document.body);
  expect(onClose).toHaveBeenCalledTimes(1);
});
