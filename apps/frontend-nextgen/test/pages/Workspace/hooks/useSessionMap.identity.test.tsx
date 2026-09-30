/** @jest-environment jsdom */
import type { SessionView } from '@/domain/collaboration';
import { useSessionMap } from '@/pages/Workspace/hooks/useSessionMap';
import { groupService } from '@/services/workspace/groupService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { render, waitFor } from '@testing-library/react';
import { useEffect } from 'react';

jest.mock('@/services/workspace/groupService');

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const gs = groupService as unknown as Record<string, jest.Mock<any>>;

function session(sessionId: string): SessionView {
  return {
    sessionId,
    groupId: 'g1',
    title: sessionId,
    kind: 'chat',
    status: 'running',
    participants: [],
    lastMessageAt: 1,
    createdAt: 1,
    favorite: false,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

it('身份切换后不会有任何已提交渲染帧携带旧身份的会话数据', async () => {
  gs.loadGroupSessionsOrBcs.mockImplementation(async (_gid: string, identityId?: string) => ({
    ok: true,
    data: [session(identityId === 'me' ? 'old-1' : 'new-1')],
  }));

  // 记录每一次「已提交」渲染中是否仍持有旧身份数据（effect 只在 commit 后执行，
  // 渲染期被丢弃的中间帧不会被记录）。
  const committed: Array<{ identity: string | null; hasStale: boolean }> = [];
  function Probe({ identity }: { identity: string | null }) {
    const map = useSessionMap('g1', ['g1'], identity);
    const hasStale = (map.rawByGroupId.g1 ?? []).some((s) => s.sessionId === 'old-1');
    useEffect(() => {
      committed.push({ identity, hasStale });
    });
    return null;
  }

  const { rerender } = render(<Probe identity="me" />);
  await waitFor(() => expect(committed.some((c) => c.identity === 'me' && c.hasStale)).toBe(true));

  rerender(<Probe identity="bot-1" />);
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalledWith('g1', 'bot-1'));
  await waitFor(() => expect(committed.some((c) => c.identity === 'bot-1' && !c.hasStale)).toBe(true));

  // 旧实现用 useEffect 清缓存：身份切换后的首帧会先提交携带 old-1 的画面（闪烁来源之一）。
  expect(committed.filter((c) => c.identity === 'bot-1' && c.hasStale)).toHaveLength(0);
});
