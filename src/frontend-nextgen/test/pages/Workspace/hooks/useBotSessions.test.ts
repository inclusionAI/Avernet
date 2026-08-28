/** @jest-environment jsdom */
import { useBotSessions } from '@/pages/Workspace/hooks/useBotSessions';
import { botSessionService, type BotChatSessionView, type ChatBotView } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botSessionService');
const svc = botSessionService as unknown as {
  listSessions: jest.Mock<any>;
  createSession: jest.Mock<any>;
  deleteSession: jest.Mock<any>;
  getSessionDetail: jest.Mock<any>;
};

const bot: ChatBotView = { botId: 'b:1', realBotId: 'b', ownerId: '1', displayName: 'B', online: true, chatable: true };
const s1: BotChatSessionView = {
  sessionId: 's1',
  botId: 'b:1',
  title: '会话1',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
};
const s2: BotChatSessionView = {
  sessionId: 's2',
  botId: 'b:1',
  title: '会话2',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
};

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
  svc.listSessions.mockResolvedValue({ ok: true, data: [s2, s1] });
  svc.createSession.mockResolvedValue({
    ok: true,
    data: { sessionId: 's3', botId: 'b:1', title: '新', messageCount: 0, gmtModified: '', gmtCreate: '' },
  });
  svc.deleteSession.mockResolvedValue({ ok: true, data: null });
  svc.getSessionDetail.mockResolvedValue({ ok: false });
});

it('展开 bot 时懒加载会话并缓存', async () => {
  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await waitFor(() => expect(svc.listSessions).toHaveBeenCalledWith(bot, 'human-1'));
  await waitFor(() => expect(result.current.sessionsByBotId['b:1']).toHaveLength(2));
});

it('createSession 选中新建会话并前置', async () => {
  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await waitFor(() => expect(result.current.sessionsByBotId['b:1']).toBeDefined());
  await result.current.createSession(bot);
  await waitFor(() => expect(result.current.selectedBotSessionId).toBe('s3'));
});

it('deleteSession 后从列表移除', async () => {
  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await waitFor(() => expect(result.current.sessionsByBotId['b:1']).toHaveLength(2));
  await result.current.deleteSession(bot, 's1');
  await waitFor(() => expect(result.current.sessionsByBotId['b:1'].find((s) => s.sessionId === 's1')).toBeUndefined());
});
