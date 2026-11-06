/** @jest-environment jsdom */
import type { SessionView } from '@/domain/collaboration';
import { useSessionMemberSync } from '@/pages/Workspace/hooks/useSessionMemberSync';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';
import { StrictMode, createElement, useState, type ReactNode } from 'react';

jest.mock('@/services/workspace/sessionService');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ss = sessionService as unknown as Record<string, jest.Mock<any>>;

type Map = Record<string, SessionView[]>;

/** 包装子 hook:用真实 React state 承载 map,applyMapUpdate 走 setState 以触发重渲染。 */
function useHarness(initial: Map, selectedId: string | null) {
  const [map, setMap] = useState<Map>(initial);
  const { updateMemberMode, updateMemberScope } = useSessionMemberSync(selectedId, setMap, 0, map);
  return { map, setMap, updateMemberMode, updateMemberScope };
}

function StrictWrapper({ children }: { children: ReactNode }) {
  // 测试文件是 .ts（非 .tsx），用 createElement 表达 StrictMode 包装，避免 JSX 解析。
  return createElement(StrictMode, null, children);
}

function sessionView(over: Partial<SessionView> = {}): SessionView {
  return {
    sessionId: 's1',
    groupId: 'g1',
    title: '一号',
    kind: 'chat',
    status: 'running',
    participants: [],
    lastMessageAt: 1,
    createdAt: 1,
    favorite: false,
    ...over,
  };
}

beforeEach(() => {
  jest.resetAllMocks();
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.setState({ activeIdentityId: 'identity_A' });
  ss.getSessionDetail.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
});

it('getSessionDetail 成功后补齐 participants(列表项无成员时)', async () => {
  ss.getSessionDetail.mockResolvedValue({
    ok: true,
    data: sessionView({
      participants: [
        { actorId: 'b1', kind: 'bot', name: 'Alpha', role: 'driver' as const, mode: 'muted' as const },
        { actorId: 'human_1', kind: 'human', name: '章梧', role: 'member' as const, mode: 'absent' as const },
      ],
      participantSummaries: [
        { actorId: 'b1', kind: 'bot', name: 'Alpha' },
        { actorId: 'human_1', kind: 'human', name: '章梧' },
      ],
      participantDetailsLoaded: true,
    }),
  });
  const initial: Map = {
    g1: [sessionView({ favorite: true })],
  };
  const { result } = renderHook(() => useHarness(initial, 's1'));
  await waitFor(() => expect(ss.getSessionDetail).toHaveBeenCalledWith('s1'));
  await waitFor(() => expect(result.current.map.g1[0].participants.length).toBe(2));
  expect(result.current.map.g1[0].participantDetailsLoaded).toBe(true);
  expect(result.current.map.g1[0].favorite).toBe(true);
});

it('已有完整成员的会话不再请求详情，state 中原 mode 保持', async () => {
  ss.getSessionDetail.mockResolvedValue({
    ok: true,
    data: sessionView({
      participants: [{ actorId: 'b1', kind: 'bot', name: 'Alpha', role: 'driver' as const, mode: 'muted' as const }],
      participantDetailsLoaded: true,
    }),
  });
  const initial: Map = {
    g1: [
      sessionView({
        participants: [{ actorId: 'b1', kind: 'bot', name: 'Alpha', role: 'driver' as const, mode: 'auto' as const }],
      }),
    ],
  };
  const { result } = renderHook(() => useHarness(initial, 's1'));
  await act(async () => {});
  expect(ss.getSessionDetail).not.toHaveBeenCalled();
  expect(result.current.map.g1[0].participants[0].mode).toBe('auto');
});

it('列表摘要已存在、详情迟到时仍应用完整 mode/scope 与新头像', async () => {
  ss.getSessionDetail.mockResolvedValue({
    ok: true,
    data: sessionView({
      participants: [
        {
          actorId: 'b1',
          kind: 'bot',
          name: 'Alpha',
          role: 'driver' as const,
          mode: 'muted' as const,
          messageViewScope: 'participant' as const,
          avatarUrl: '/new.png',
        },
      ],
      participantSummaries: [{ actorId: 'b1', kind: 'bot', name: 'Alpha', avatarUrl: '/new.png' }],
      participantDetailsLoaded: true,
    }),
  });
  const initial: Map = {
    g1: [
      sessionView({
        participantSummaries: [{ actorId: 'b1', kind: 'bot', name: 'Alpha', avatarUrl: '/old.png' }],
        participantDetailsLoaded: false,
      }),
    ],
  };
  const { result } = renderHook(() => useHarness(initial, 's1'));

  // 摘要非空但细节未加载，仍必须请求详情。
  await waitFor(() => expect(ss.getSessionDetail).toHaveBeenCalledWith('s1'));
  await waitFor(() => expect(result.current.map.g1[0].participants[0]?.mode).toBe('muted'));
  expect(result.current.map.g1[0].participants[0].messageViewScope).toBe('participant');
  expect(result.current.map.g1[0].participantSummaries?.[0].avatarUrl).toBe('/new.png');
  expect(result.current.map.g1[0].participantDetailsLoaded).toBe(true);
});

it('明确空详情清空展示并标记已加载（StrictMode 下不丢更新也不无限重拉）', async () => {
  ss.getSessionDetail.mockResolvedValue({
    ok: true,
    data: sessionView({ participants: [], participantSummaries: [], participantDetailsLoaded: true }),
  });
  const initial: Map = {
    g1: [
      sessionView({
        participantSummaries: [{ actorId: 'b1', kind: 'bot', name: 'Alpha', avatarUrl: '/b1.png' }],
        participantDetailsLoaded: false,
      }),
    ],
  };
  const { result } = renderHook(() => useHarness(initial, 's1'), { wrapper: StrictWrapper });

  await waitFor(() => expect(result.current.map.g1[0].participantDetailsLoaded).toBe(true));
  expect(result.current.map.g1[0].participants).toEqual([]);
  expect(result.current.map.g1[0].participantSummaries).toEqual([]);

  await act(async () => {
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 20);
    });
  });
  // StrictMode 会双跑挂载 effect（首个被 cancelled），但已加载标记阻止后续无限重拉。
  expect(ss.getSessionDetail.mock.calls.length).toBeLessThanOrEqual(2);
});

it('列表重新加载后请求新详情，不重放旧 pending detail', async () => {
  ss.getSessionDetail
    .mockResolvedValueOnce({
      ok: true,
      data: sessionView({
        participants: [{ actorId: 'old', kind: 'bot', name: 'Old', role: 'driver' as const, mode: 'auto' as const }],
        participantSummaries: [{ actorId: 'old', kind: 'bot', name: 'Old' }],
        participantDetailsLoaded: true,
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      data: sessionView({
        participants: [{ actorId: 'new', kind: 'bot', name: 'New', role: 'driver' as const, mode: 'muted' as const }],
        participantSummaries: [{ actorId: 'new', kind: 'bot', name: 'New' }],
        participantDetailsLoaded: true,
      }),
    });

  const initial: Map = { g1: [sessionView({ participantDetailsLoaded: false })] };
  const { result } = renderHook(() => useHarness(initial, 's1'));

  await waitFor(() => expect(result.current.map.g1[0].participants.map((p) => p.actorId)).toEqual(['old']));
  expect(ss.getSessionDetail).toHaveBeenCalledTimes(1);

  await act(async () => {
    result.current.setMap({
      g1: [
        sessionView({
          participantSummaries: [{ actorId: 'new', kind: 'bot', name: 'New' }],
          participantDetailsLoaded: false,
        }),
      ],
    });
  });

  await waitFor(() => expect(result.current.map.g1[0].participants.map((p) => p.actorId)).toEqual(['new']));
  expect(ss.getSessionDetail).toHaveBeenCalledTimes(2);
});

it('切换身份后的旧详情不能写回', async () => {
  let resolveOld!: (value: unknown) => void;
  ss.getSessionDetail
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockImplementation(() => new Promise(() => {}));

  const initial: Map = { g1: [sessionView({ participantDetailsLoaded: false })] };
  const { result } = renderHook(() => useHarness(initial, 's1'));
  await waitFor(() => expect(ss.getSessionDetail).toHaveBeenCalledTimes(1));

  await act(async () => {
    useWorkspaceStore.setState({ activeIdentityId: 'identity_B' });
  });
  await waitFor(() => expect(ss.getSessionDetail).toHaveBeenCalledTimes(2));

  await act(async () => {
    resolveOld({
      ok: true,
      data: sessionView({
        participants: [{ actorId: 'stale', kind: 'bot', name: 'Stale', role: 'driver' as const, mode: 'auto' as const }],
        participantSummaries: [{ actorId: 'stale', kind: 'bot', name: 'Stale' }],
        participantDetailsLoaded: true,
      }),
    });
  });

  expect(result.current.map.g1[0].participants).toEqual([]);
  expect(result.current.map.g1[0].participantDetailsLoaded).toBe(false);
});

it('updateMemberMode 成功后用 PATCH 响应刷新对应会话 participants', async () => {
  ss.updateMemberMode.mockResolvedValue({
    ok: true,
    data: sessionView({
      participants: [{ actorId: 'b1', kind: 'bot', name: 'Alpha', role: 'driver' as const, mode: 'muted' as const }],
      participantDetailsLoaded: true,
    }),
  });
  const initial: Map = {
    g1: [sessionView({ favorite: true })],
  };
  const { result } = renderHook(() => useHarness(initial, 's1'));
  const ok = await act(() => result.current.updateMemberMode('s1', 'b1', 'muted'));
  expect(ok).toBe(true);
  expect(ss.updateMemberMode).toHaveBeenCalledWith('s1', 'b1', 'muted', undefined);
  await waitFor(() => {
    expect(result.current.map.g1[0].participants.find((p) => p.actorId === 'b1')?.mode).toBe('muted');
  });
  expect(result.current.map.g1[0].groupId).toBe('g1');
  expect(result.current.map.g1[0].favorite).toBe(true);
});

it('detail 返回早于 session 列表或列表后到时，participants 不被丢弃', async () => {
  ss.getSessionDetail.mockResolvedValue({
    ok: true,
    data: sessionView({
      participants: [
        { actorId: 'b1', kind: 'bot' as const, name: 'Alpha', role: 'driver' as const, mode: 'auto' as const },
        { actorId: 'human_1', kind: 'human' as const, name: '章梧', role: 'member' as const, mode: 'present' as const },
      ],
      participantSummaries: [
        { actorId: 'b1', kind: 'bot' as const, name: 'Alpha' },
        { actorId: 'human_1', kind: 'human' as const, name: '章梧' },
      ],
      participantDetailsLoaded: true,
    }),
  });
  const emptySession: SessionView = sessionView({ favorite: true, lastMessageAt: 2, createdAt: 2, participantDetailsLoaded: false });
  const { result } = renderHook(() => useHarness({}, 's1'));
  await waitFor(() => expect(result.current.map.g1?.[0]?.participants.length).toBe(2));

  await act(async () => {
    result.current.setMap({ g1: [emptySession] });
  });
  await waitFor(() => expect(result.current.map.g1[0].participants.length).toBe(2));
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

it('updateMemberScope 成功后就地刷新对应会话的 scope 回显', async () => {
  ss.updateMemberScope.mockResolvedValue({
    ok: true,
    data: sessionView({
      participants: [
        {
          actorId: 'human_1',
          kind: 'human' as const,
          name: '章梧',
          role: 'member' as const,
          mode: 'present' as const,
          messageViewScope: 'participant' as const,
        },
      ],
      participantDetailsLoaded: true,
    }),
  });
  const initial: Map = {
    g1: [sessionView({ favorite: true })],
  };
  const { result } = renderHook(() => useHarness(initial, 's1'));
  const ok = await act(() => result.current.updateMemberScope('s1', 'human_1', 'participant'));
  expect(ok).toBe(true);
  expect(ss.updateMemberScope).toHaveBeenCalledWith('s1', 'human_1', 'participant');
  await waitFor(() => {
    expect(result.current.map.g1[0].participants.find((p) => p.actorId === 'human_1')?.messageViewScope).toBe(
      'participant',
    );
  });
  expect(result.current.map.g1[0].favorite).toBe(true);
});

it('updateMemberScope 失败时返回 false', async () => {
  ss.updateMemberScope.mockResolvedValue({
    ok: false,
    error: { code: 'X', friendlyMessage: '切换消息视角失败', canRetry: false },
  });
  const { result } = renderHook(() => useHarness({}, 's1'));
  const ok = await act(() => result.current.updateMemberScope('s1', 'human_1', 'participant'));
  expect(ok).toBe(false);
});
