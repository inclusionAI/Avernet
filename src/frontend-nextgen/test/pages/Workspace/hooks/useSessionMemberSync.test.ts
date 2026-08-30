/** @jest-environment jsdom */
import type { SessionView } from '@/domain/collaboration';
import { useSessionMemberSync } from '@/pages/Workspace/hooks/useSessionMemberSync';
import { sessionService } from '@/services/workspace/sessionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useState } from 'react';

jest.mock('@/services/workspace/sessionService');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ss = sessionService as unknown as Record<string, jest.Mock<any>>;

type Map = Record<string, SessionView[]>;

/** 包装子 hook:用真实 React state 承载 map,applyMapUpdate 走 setState 以触发重渲染。 */
function useHarness(initial: Map, selectedId: string | null) {
  const [map, setMap] = useState<Map>(initial);
  const { updateMemberMode } = useSessionMemberSync(selectedId, setMap, 0);
  return { map, updateMemberMode };
}

beforeEach(() => {
  jest.resetAllMocks();
  ss.getSessionDetail.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
});

it('getSessionDetail 成功后补齐 participants(列表项无成员时)', async () => {
  ss.getSessionDetail.mockResolvedValue({
    ok: true,
    data: {
      sessionId: 's1',
      groupId: 'g1',
      title: '一号',
      kind: 'chat',
      status: 'running',
      participants: [
        { actorId: 'b1', kind: 'bot', name: 'Alpha', role: 'driver' as const, mode: 'muted' as const },
        { actorId: 'human_1', kind: 'human', name: '章梧', role: 'member' as const, mode: 'absent' as const },
      ],
      lastMessageAt: 1,
      createdAt: 1,
      favorite: false,
    },
  });
  const initial: Map = {
    g1: [
      {
        sessionId: 's1',
        groupId: 'g1',
        title: '一号',
        kind: 'chat',
        status: 'running',
        participants: [],
        lastMessageAt: 1,
        createdAt: 1,
        favorite: true,
      },
    ],
  };
  const { result } = renderHook(() => useHarness(initial, 's1'));
  await waitFor(() => expect(ss.getSessionDetail).toHaveBeenCalledWith('s1'));
  await waitFor(() => expect(result.current.map.g1[0].participants.length).toBe(2));
});

it('已有 participants 的会话不被 getSessionDetail 覆盖', async () => {
  ss.getSessionDetail.mockResolvedValue({
    ok: true,
    data: {
      sessionId: 's1',
      groupId: 'g1',
      title: '一号',
      kind: 'chat',
      status: 'running',
      participants: [{ actorId: 'b1', kind: 'bot', name: 'Alpha', role: 'driver' as const, mode: 'muted' as const }],
      lastMessageAt: 1,
      createdAt: 1,
      favorite: false,
    },
  });
  const initial: Map = {
    g1: [
      {
        sessionId: 's1',
        groupId: 'g1',
        title: '一号',
        kind: 'chat',
        status: 'running',
        participants: [{ actorId: 'b1', kind: 'bot', name: 'Alpha', role: 'driver' as const, mode: 'auto' as const }],
        lastMessageAt: 1,
        createdAt: 1,
        favorite: false,
      },
    ],
  };
  renderHook(() => useHarness(initial, 's1'));
  await waitFor(() => expect(ss.getSessionDetail).toHaveBeenCalled());
  // applyMapUpdate 返回 cur 不触发 setState,participants 保持 auto
  expect(ss.getSessionDetail).toHaveBeenCalled();
});

it('updateMemberMode 成功后用 PATCH 响应刷新对应会话 participants', async () => {
  ss.updateMemberMode.mockResolvedValue({
    ok: true,
    data: {
      sessionId: 's1',
      groupId: 'g1',
      title: '一号',
      kind: 'chat',
      status: 'running',
      participants: [{ actorId: 'b1', kind: 'bot', name: 'Alpha', role: 'driver' as const, mode: 'muted' as const }],
      lastMessageAt: 1,
      createdAt: 1,
      favorite: false,
    },
  });
  const initial: Map = {
    g1: [
      {
        sessionId: 's1',
        groupId: 'g1',
        title: '一号',
        kind: 'chat',
        status: 'running',
        participants: [],
        lastMessageAt: 1,
        createdAt: 1,
        favorite: true,
      },
    ],
  };
  const { result } = renderHook(() => useHarness(initial, 's1'));
  const ok = await act(() => result.current.updateMemberMode('s1', 'b1', 'muted'));
  expect(ok).toBe(true);
  expect(ss.updateMemberMode).toHaveBeenCalledWith('s1', 'b1', 'muted');
  await waitFor(() => {
    expect(result.current.map.g1[0].participants.find((p) => p.actorId === 'b1')?.mode).toBe('muted');
  });
  expect(result.current.map.g1[0].groupId).toBe('g1');
  expect(result.current.map.g1[0].favorite).toBe(true);
});

it('updateMemberMode 失败时返回 false', async () => {
  ss.updateMemberMode.mockResolvedValue({
    ok: false,
    error: { code: 'X', friendlyMessage: '更新会话成员状态失败', canRetry: false },
  });
  const { result } = renderHook(() => useHarness({}, 's1'));
  const ok = await act(() => result.current.updateMemberMode('s1', 'b1', 'muted'));
  expect(ok).toBe(false);
});
