/** @jest-environment jsdom */
import { useTaskEscortWorkspaceScope } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceScope';
import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { identityService } from '@/services/workspace/identityService';
import { applyIdentityLoadResult } from '@/services/workspace/identityStore';
import { act, renderHook, waitFor } from '@testing-library/react';
jest.mock('@/hooks/useBotWorkshopEditorIdentity', () => ({ useBotWorkshopRequestIdentity: jest.fn() }));
jest.mock('@/hooks/useSpaceContext', () => ({
  useSpaceContext: (selector: (value: { currentSpaceId: number }) => unknown) => selector({ currentSpaceId: 1 }),
}));
jest.mock('@/services/workspace/identityService', () => ({ identityService: { loadIdentities: jest.fn() } }));
jest.mock('@/services/workspace/identityStore', () => ({ applyIdentityLoadResult: jest.fn() }));
it('身份读取失败可以重试，并通过现有统一写回器恢复身份', async () => {
  jest.mocked(useBotWorkshopRequestIdentity).mockReturnValue({ ready: false, loading: false, error: '身份加载失败' });
  const loaded = { identities: [], defaultActiveId: 'user-a' };
  jest.mocked(identityService.loadIdentities).mockResolvedValue({ ok: true, data: loaded });
  const { result } = renderHook(() => useTaskEscortWorkspaceScope());
  await act(async () => {
    result.current.retry();
  });
  expect(identityService.loadIdentities).toHaveBeenCalledTimes(1);
  expect(applyIdentityLoadResult).toHaveBeenCalledWith(loaded);
});
it('重试再次失败保留明确错误且结束 loading', async () => {
  jest.mocked(useBotWorkshopRequestIdentity).mockReturnValue({ ready: false, loading: false, error: '身份加载失败' });
  jest.mocked(identityService.loadIdentities).mockResolvedValue({
    ok: false,
    error: { code: 'unavailable', canRetry: true, friendlyMessage: '身份服务暂不可用' },
  });
  const { result } = renderHook(() => useTaskEscortWorkspaceScope());
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.error).toBe('身份服务暂不可用'));
  expect(result.current.loading).toBe(false);
});
