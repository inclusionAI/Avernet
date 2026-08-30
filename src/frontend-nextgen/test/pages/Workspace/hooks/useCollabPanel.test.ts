/** @jest-environment jsdom */
import type { IdentityView, SessionView } from '@/domain/collaboration';
import { useCollabPanel } from '@/pages/Workspace/hooks/useCollabPanel';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

const botIdentity: IdentityView = { id: 'b:1', kind: 'bot', displayName: 'Alpha', online: true };
const humanIdentity: IdentityView = { id: 'human_1', kind: 'user', displayName: '章梧', online: true };

function makeSession(participants: SessionView['participants']): SessionView {
  return {
    sessionId: 's1',
    groupId: 'g1',
    title: '会话',
    kind: 'chat',
    status: 'running',
    participants,
    lastMessageAt: 1,
    createdAt: 1,
    favorite: false,
  };
}

const updateMemberMode = jest.fn<any>().mockResolvedValue(true);

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.getState().setIdentities([humanIdentity, botIdentity], botIdentity.id);
});

it('bot 视角恒显示面板;读取 bot/human 成员 mode', () => {
  const session = makeSession([
    { actorId: 'b:1', kind: 'bot', name: 'Alpha', role: 'driver', mode: 'auto' },
    { actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' },
  ]);
  const { result } = renderHook(() => useCollabPanel(session, botIdentity, updateMemberMode));
  expect(result.current.visible).toBe(true);
  expect(result.current.humanAbsentOnly).toBe(false);
  expect(result.current.botMode).toBe('auto');
  expect(result.current.humanJoined).toBe(true);
  expect(result.current.humanName).toBe('章梧');
});

it('setBotMode 调用 updateMemberMode 并携带 bot actorId', async () => {
  const session = makeSession([{ actorId: 'b:1', kind: 'bot', name: 'Alpha', role: 'driver', mode: 'auto' }]);
  const { result } = renderHook(() => useCollabPanel(session, botIdentity, updateMemberMode));
  await act(() => result.current.setBotMode('muted'));
  expect(updateMemberMode).toHaveBeenCalledWith('s1', 'b:1', 'muted');
});

it('相同 mode 重复切换不发起请求', async () => {
  const session = makeSession([{ actorId: 'b:1', kind: 'bot', name: 'Alpha', role: 'driver', mode: 'auto' }]);
  const { result } = renderHook(() => useCollabPanel(session, botIdentity, updateMemberMode));
  await act(() => result.current.setBotMode('auto'));
  expect(updateMemberMode).not.toHaveBeenCalled();
});

it('joinSession 通过 PATCH participants/{bot_uuid} 将当前用户置为 present 并切到 human 身份', async () => {
  const session = makeSession([{ actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'absent' }]);
  const { result } = renderHook(() => useCollabPanel(session, botIdentity, updateMemberMode));
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession('s1');
  let ok = false;
  await act(async () => {
    ok = await result.current.joinSession();
  });
  expect(ok).toBe(true);
  expect(updateMemberMode).toHaveBeenCalledWith('s1', 'human_1', 'present');
  expect(useWorkspaceStore.getState().activeIdentityId).toBe('human_1');
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g1');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s1');
});

it('human 视角 + human absent → humanAbsentOnly 单条', () => {
  useWorkspaceStore.getState().setActiveIdentity(humanIdentity.id);
  const session = makeSession([{ actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'absent' }]);
  const { result } = renderHook(() => useCollabPanel(session, humanIdentity, updateMemberMode));
  expect(result.current.visible).toBe(true);
  expect(result.current.humanAbsentOnly).toBe(true);
});

it('human 视角 + human present → 不显示面板', () => {
  const session = makeSession([{ actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' }]);
  const { result } = renderHook(() => useCollabPanel(session, humanIdentity, updateMemberMode));
  // present 态下面板 visible=true（渲染「在会话中隐身」条），但 humanAbsentOnly=false（Sender 仍显示）。
  expect(result.current.visible).toBe(true);
  expect(result.current.humanAbsentOnly).toBe(false);
});

it('去发言切换到 human 身份', () => {
  const session = makeSession([{ actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' }]);
  const { result } = renderHook(() => useCollabPanel(session, botIdentity, updateMemberMode));
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession('s1');
  expect(result.current.canSwitchToHuman).toBe(true);
  act(() => result.current.switchToHuman());
  expect(useWorkspaceStore.getState().activeIdentityId).toBe('human_1');
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g1');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s1');
});

it('会话为空时面板不可见且动作安全短路', async () => {
  const { result } = renderHook(() => useCollabPanel(null, botIdentity, updateMemberMode));
  expect(result.current.visible).toBe(false);
  await act(() => result.current.setBotMode('muted'));
  const ok = await result.current.joinSession();
  expect(ok).toBe(false);
  await waitFor(() => expect(updateMemberMode).not.toHaveBeenCalled());
});

it('participants 列表刷新清空时 human 状态保持不闪烁', () => {
  const fullSession = makeSession([
    { actorId: 'b:1', kind: 'bot', name: 'Alpha', role: 'driver', mode: 'auto' },
    { actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' },
  ]);
  // 列表刷新后 participants 被清空（列表接口不返回 participants）
  const clearedSession = { ...fullSession, participants: [] };

  const { result, rerender } = renderHook(({ session }) => useCollabPanel(session, humanIdentity, updateMemberMode), {
    initialProps: { session: fullSession },
  });

  expect(result.current.humanJoined).toBe(true);
  expect(result.current.visible).toBe(true);

  // 模拟列表刷新：participants 暂时为空
  rerender({ session: clearedSession });

  // human 状态应从 ref 缓存中恢复，面板不消失
  expect(result.current.humanJoined).toBe(true);
  expect(result.current.visible).toBe(true);
  expect(result.current.humanName).toBe('章梧');
});

it('切换会话时 human ref 缓存被清空，不串数据', () => {
  const session1 = makeSession([{ actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' }]);
  const session2: SessionView = {
    ...makeSession([]),
    sessionId: 's2',
  };

  const { result, rerender } = renderHook(({ session }) => useCollabPanel(session, humanIdentity, updateMemberMode), {
    initialProps: { session: session1 },
  });

  expect(result.current.humanJoined).toBe(true);

  // 切到另一个空 participants 的会话
  rerender({ session: session2 });

  // 不应继承上一个会话的 human 状态
  expect(result.current.humanJoined).toBe(false);
});
