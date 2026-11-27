/** @jest-environment jsdom */
import type { IdentityView, SessionView } from '@/domain/collaboration';
import { useGroupSessions } from '@/pages/Workspace/hooks/useGroupSessions';
import { useWorkspacePage } from '@/pages/Workspace/hooks/useWorkspacePage';
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import { groupService } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook, waitFor } from '@testing-library/react';
import { history, useSearchParams } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { replace: jest.fn() }, useSearchParams: jest.fn() }));
jest.mock('@/services/backendApi/collaboration/sessionController');
jest.mock('@/services/workspace/groupService');

const controller = jest.mocked(sessionController);
const groups = jest.mocked(groupService);
const identities: IdentityView[] = [{ id: 'bot-self:owner', kind: 'bot', displayName: 'Bot', online: true }];
const departed: SessionView = {
  sessionId: 'leave-refresh',
  groupId: 'g1',
  title: '退出的会话',
  kind: 'chat',
  status: 'running',
  participants: [],
  participantDetailsLoaded: true,
  lastMessageAt: 0,
  createdAt: 0,
  favorite: false,
};
const remaining: SessionView = { ...departed, sessionId: 'remaining', title: '剩余会话' };

beforeEach(() => {
  jest.resetAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.getState().setIdentities(identities, identities[0].id);
  window.history.replaceState(
    null,
    '',
    '/workspace/collaboration?current=bot-self%3Aowner&group=g1&session=leave-refresh',
  );
  jest.mocked(useSearchParams).mockImplementation(() => [new URLSearchParams(window.location.search), jest.fn()]);
  jest.mocked(history.replace).mockImplementation((path) => {
    window.history.replaceState(null, '', String(path));
  });
  controller.deleteSessionParticipant.mockResolvedValue({ code: 20000, data: { deleted: true } });
  // 用真实 sessionService：若 DELETE 后误读详情，会得到与线上相同的拒绝。
  controller.getSession.mockRejectedValue({ status: 403 });
});

it.each([true, false])('退出成功后刷新不再恢复或查询已退出会话（存在其他会话：%s）', async (hasRemaining) => {
  groups.loadGroupSessionsOrBcs.mockResolvedValue({
    ok: true,
    data: {
      items: hasRemaining ? [departed, remaining] : [departed],
      offset: 0,
      limit: 10,
      total: hasRemaining ? 2 : 1,
      hasMore: false,
    },
  });
  const page = renderHook(() => useWorkspacePage());
  await waitFor(() => expect(page.result.current.urlHydrated).toBe(true));
  const sessions = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(sessions.result.current.sessions).toHaveLength(hasRemaining ? 2 : 1));

  controller.getSession.mockClear();
  let ok = false;
  await act(async () => {
    ok = await sessions.result.current.leaveSession(departed.sessionId, 'bot-self');
  });

  expect(ok).toBe(true);
  expect(controller.getSession).not.toHaveBeenCalledWith(departed.sessionId);
  expect(controller.deleteSessionParticipant).toHaveBeenCalledWith(departed.sessionId, 'bot-self');
  const nextSessionId = hasRemaining ? remaining.sessionId : null;
  await waitFor(() => expect(new URLSearchParams(window.location.search).get('session')).toBe(nextSessionId));
  expect(useWorkspaceStore.getState().selectedSessionId).toBe(nextSessionId);
  expect(sessions.result.current.sessions.map((session) => session.sessionId)).not.toContain(departed.sessionId);
  sessions.unmount();
  page.unmount();

  // 模拟整页刷新：Store 重建，仅凭退出后 URL 恢复，不依赖「已退出」内存登记。
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.getState().setIdentities(identities, identities[0].id);
  const refreshedPage = renderHook(() => useWorkspacePage());
  await waitFor(() => expect(refreshedPage.result.current.urlHydrated).toBe(true));
  expect(useWorkspaceStore.getState().selectedSessionId).toBe(nextSessionId);
  expect(controller.getSession).not.toHaveBeenCalledWith(departed.sessionId);
});

it('退出请求失败时保留列表、选中态和 URL', async () => {
  groups.loadGroupSessionsOrBcs.mockResolvedValue({
    ok: true,
    data: { items: [departed], offset: 0, limit: 10, total: 1, hasMore: false },
  });
  controller.deleteSessionParticipant.mockRejectedValue({ status: 403 });
  const page = renderHook(() => useWorkspacePage());
  await waitFor(() => expect(page.result.current.urlHydrated).toBe(true));
  const sessions = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(sessions.result.current.sessions).toHaveLength(1));
  controller.getSession.mockClear();

  let ok = true;
  await act(async () => {
    ok = await sessions.result.current.leaveSession(departed.sessionId, 'bot-self');
  });

  expect(ok).toBe(false);
  expect(sessions.result.current.sessions[0].sessionId).toBe(departed.sessionId);
  expect(useWorkspaceStore.getState().selectedSessionId).toBe(departed.sessionId);
  expect(new URLSearchParams(window.location.search).get('session')).toBe(departed.sessionId);
  expect(controller.getSession).not.toHaveBeenCalled();
});
