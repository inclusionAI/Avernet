/** @jest-environment jsdom */
import { useBotSessionMap } from '@/pages/Workspace/hooks/useBotSessionMap';
import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { render, waitFor } from '@testing-library/react';
import { useEffect } from 'react';

jest.mock('@/services/workspace/botSessionService');

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const svc = botSessionService as unknown as Record<string, jest.Mock<any>>;

const bot: ChatBotView = { botId: 'b:1', realBotId: 'b', ownerId: '1', displayName: 'B', online: true, chatable: true };

function botSession(sessionId: string) {
  return { sessionId, botId: 'b:1', title: sessionId, messageCount: 0, gmtModified: '', gmtCreate: '' };
}

beforeEach(() => {
  jest.clearAllMocks();
  svc.getSessionDetail.mockResolvedValue({ ok: false });
  svc.listFavoriteSessionsPage.mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
});

it('身份切换后不会有任何已提交渲染帧携带旧身份的 bot 会话数据', async () => {
  svc.listSessionsPage.mockImplementation(async (_b: unknown, userId: string) => ({
    ok: true,
    data: { items: [botSession(userId === 'me' ? 'old-1' : 'new-1')], total: 1 },
  }));

  // 记录每一次「已提交」渲染中是否仍持有旧身份数据（effect 只在 commit 后执行，
  // 渲染期被丢弃的中间帧不会被记录）。
  const committed: Array<{ identity: string | null; hasStale: boolean }> = [];
  function Probe({ identity }: { identity: string | null }) {
    const map = useBotSessionMap([bot], ['b:1'], identity);
    const hasStale = (map.rawByBotId['b:1'] ?? []).some((s) => s.sessionId === 'old-1');
    useEffect(() => {
      committed.push({ identity, hasStale });
    });
    return null;
  }

  const { rerender } = render(<Probe identity="me" />);
  await waitFor(() => expect(committed.some((c) => c.identity === 'me' && c.hasStale)).toBe(true));

  rerender(<Probe identity="u2" />);
  await waitFor(() => expect(committed.some((c) => c.identity === 'u2' && !c.hasStale)).toBe(true));

  // 旧实现用 useEffect 清缓存：身份切换后的首帧会先提交携带 old-1 的画面（闪烁来源）。
  expect(committed.filter((c) => c.identity === 'u2' && c.hasStale)).toHaveLength(0);
});
