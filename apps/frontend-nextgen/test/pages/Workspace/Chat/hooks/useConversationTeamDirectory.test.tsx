/** @jest-environment jsdom */
import { useConversationTeamDirectory } from '@/pages/Workspace/Chat/hooks/useConversationTeamDirectory';
import { teamBotConversationService } from '@/services/workspace/teamBotConversationService';
import { act, renderHook, waitFor } from '@testing-library/react';
jest.mock('@/services/workspace/teamBotConversationService', () => ({
  teamBotConversationService: { listBots: jest.fn() },
}));
const list = jest.mocked(teamBotConversationService.listBots);
const bot = {
  botId: 'shared:entity',
  realBotId: 'shared',
  ownerId: 'entity',
  displayName: '团队',
  online: true,
  chatable: true,
};
beforeEach(() => list.mockReset());
test('独立加载、错误与重试；用户切换/登出清空，不使用 workspace 工作身份', async () => {
  list
    .mockResolvedValueOnce({ ok: false, error: { code: 'FAIL', friendlyMessage: '团队加载失败', canRetry: true } })
    .mockResolvedValue({ ok: true, data: [bot] });
  const { result, rerender } = renderHook(
    ({ userId }: { userId: string | null }) => useConversationTeamDirectory(userId),
    { initialProps: { userId: 'viewer' as string | null } },
  );
  await waitFor(() => expect(result.current.teamError).toBe('团队加载失败'));
  act(() => result.current.retryTeam());
  await waitFor(() => expect(result.current.teamBots).toEqual([{ section: 'team', bot }]));
  expect(list).toHaveBeenCalledWith('viewer', expect.any(AbortSignal));
  rerender({ userId: null });
  expect(result.current.teamBots).toEqual([]);
  expect(result.current.teamLoading).toBe(false);
});
test('旧登录用户请求晚到不覆盖新目录，卸载取消请求', async () => {
  let resolveOld!: (value: Awaited<ReturnType<typeof teamBotConversationService.listBots>>) => void;
  list
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValue({ ok: true, data: [] });
  const { result, rerender, unmount } = renderHook(({ id }) => useConversationTeamDirectory(id), {
    initialProps: { id: 'old' },
  });
  const signal = list.mock.calls[0][1]!;
  rerender({ id: 'new' });
  await waitFor(() => expect(result.current.teamLoading).toBe(false));
  await act(async () => resolveOld({ ok: true, data: [bot] }));
  expect(result.current.teamBots).toEqual([]);
  expect(signal.aborted).toBe(true);
  const currentSignal = list.mock.calls[1][1]!;
  unmount();
  expect(currentSignal.aborted).toBe(true);
});
