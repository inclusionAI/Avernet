/** @jest-environment jsdom */
import { useBotWorkshopEditorIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useIdentityStore } from '@/stores/identityStore';
import { renderHook, waitFor } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));

const mockedUseHumanIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;

afterEach(() => {
  jest.clearAllMocks();
  useIdentityStore.getState().reset();
});

it('预发 Tern 身份就绪后同步 user_id 并开放编辑页请求', async () => {
  mockedUseHumanIdentity.mockReturnValue({
    status: 'ready',
    identity: { userId: '327325', displayName: '测试用户', online: true },
  });

  const { result } = renderHook(() => useBotWorkshopEditorIdentity());

  expect(result.current.ready).toBe(true);
  await waitFor(() => expect(useIdentityStore.getState().currentIdentityId).toBe('327325'));
});

it('身份加载期间暂停编辑页请求', () => {
  mockedUseHumanIdentity.mockReturnValue({ status: 'loading', identity: null });

  const { result } = renderHook(() => useBotWorkshopEditorIdentity());

  expect(result.current).toEqual({ ready: false, loading: true, error: undefined });
  expect(useIdentityStore.getState().currentIdentityId).toBeUndefined();
});

it('身份解析失败时返回可展示错误', () => {
  mockedUseHumanIdentity.mockReturnValue({ status: 'error', identity: null, error: '身份未加载' });

  const { result } = renderHook(() => useBotWorkshopEditorIdentity());

  expect(result.current).toEqual({ ready: false, loading: false, error: '身份未加载' });
});
