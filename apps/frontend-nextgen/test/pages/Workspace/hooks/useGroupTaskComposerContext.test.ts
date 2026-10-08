/** @jest-environment jsdom */
import type { GroupView, IdentityView, SessionView } from '@/domain/collaboration';
import { useGroupTaskComposerContext } from '@/pages/Workspace/hooks/useGroupTaskComposerContext';
import { renderHook } from '@testing-library/react';

const session: SessionView = {
  sessionId: 'session-1',
  groupId: 'group-1',
  title: '接力协作',
  kind: 'chat',
  status: 'running',
  participants: [],
  lastMessageAt: 0,
  createdAt: 0,
  favorite: false,
};
const user: IdentityView = { id: 'user-1', kind: 'user', displayName: '用户', online: true };
const group: GroupView = {
  groupId: 'group-1',
  name: '接力群',
  kind: 'task_master_slave',
  status: 'active',
  driverBotUuid: 'manager-bot',
  participants: [
    { actorId: 'worker-bot', kind: 'bot', name: 'Worker', role: 'worker', mode: 'auto' },
    { actorId: 'manager-bot', kind: 'bot', name: 'Manager', role: 'manager', mode: 'auto' },
  ],
  sessions: [session],
  lastMessageAt: 0,
  createdAt: 0,
  participantCount: 2,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

describe('useGroupTaskComposerContext', () => {
  it('使用明确的 driver/manager 作为任务 owner 与 relay holder，而不是首个 Bot', () => {
    const { result } = renderHook(() => useGroupTaskComposerContext(group, session, user));

    expect(result.current?.ownerBotId).toBe('manager-bot');
  });

  it('缺少 driverBotUuid 时优先回退 driver/manager 角色', () => {
    const { result } = renderHook(() =>
      useGroupTaskComposerContext({ ...group, driverBotUuid: undefined }, session, user),
    );

    expect(result.current?.ownerBotId).toBe('manager-bot');
  });
});
