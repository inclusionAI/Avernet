/** @jest-environment jsdom */
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useSquareIdentity } from '@/hooks/useSquareIdentity';
import { identityService } from '@/services/workspace/identityService';
import { useSquareIdentityStore } from '@/stores/squareIdentityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/services/workspace/identityService', () => ({
  identityService: {
    loadIdentities: jest.fn(),
    isIdentityLoading: jest.fn(() => false),
    isIdentityResolved: jest.fn(() => false),
  },
}));

const mockedUseHumanIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;
const mockedLoadIdentities = identityService.loadIdentities as jest.MockedFunction<
  typeof identityService.loadIdentities
>;

const IDENTITIES = [
  { id: 'human_900003', kind: 'user' as const, displayName: '当前用户', online: true },
  { id: 'bot-1:900003', kind: 'bot' as const, displayName: '当前 Bot', online: true },
  { id: 'bot-2:900003', kind: 'bot' as const, displayName: '备用 Bot', online: true },
];

describe('useSquareIdentity', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useWorkspaceStore.getState().reset();
    useSquareIdentityStore.getState().reset();
    useWorkspaceStore.getState().setIdentities(IDENTITIES, 'human_900003');
    mockedUseHumanIdentity.mockReturnValue({
      identity: { userId: '900003', displayName: '当前用户', online: true },
      status: 'ready',
    });
  });

  it('未选择时默认映射登录用户身份项，列表就绪', () => {
    const { result } = renderHook(() => useSquareIdentity());

    expect(result.current.selectedIdentityId).toBeNull();
    expect(result.current.selectedIdentity).toMatchObject({ id: 'human_900003', kind: 'user' });
    expect(result.current.listStatus).toBe('ready');
    expect(result.current.identities).toHaveLength(3);
  });

  it('userIdentity 恒为列表用户身份项，不受模块级选择影响（供公开协作群/任务广场固定身份）', () => {
    useSquareIdentityStore.getState().selectIdentity('bot-1:900003');

    const { result } = renderHook(() => useSquareIdentity());

    expect(result.current.userIdentity).toMatchObject({ id: 'human_900003', kind: 'user' });
    expect(result.current.selectedIdentity).toMatchObject({ id: 'bot-1:900003', kind: 'bot' });
  });

  it('持久化身份有效时返回对应 Bot 身份项', () => {
    useSquareIdentityStore.getState().selectIdentity('bot-2:900003');

    const { result } = renderHook(() => useSquareIdentity());

    expect(result.current.selectedIdentityId).toBe('bot-2:900003');
    expect(result.current.selectedIdentity).toMatchObject({ id: 'bot-2:900003', kind: 'bot' });
  });

  it('持久化身份失效（不在列表）时自动回退登录用户身份并清除持久化', async () => {
    useSquareIdentityStore.getState().selectIdentity('bot-gone:900003');

    const { result } = renderHook(() => useSquareIdentity());

    await waitFor(() => expect(result.current.selectedIdentityId).toBeNull());
    expect(result.current.selectedIdentity).toMatchObject({ id: 'human_900003', kind: 'user' });
    expect(window.localStorage.getItem('teamclaw:square:identityId')).toBeNull();
  });

  it('身份列表尚未加载（loading）时返回 loading 态', () => {
    useWorkspaceStore.getState().reset();
    mockedUseHumanIdentity.mockReturnValue({ identity: null, status: 'loading' });

    const { result } = renderHook(() => useSquareIdentity());

    expect(result.current.listStatus).toBe('loading');
    expect(result.current.selectedIdentity).toBeNull();
  });

  it('身份加载失败（error）且列表为空时返回 error 态', () => {
    useWorkspaceStore.getState().reset();
    mockedUseHumanIdentity.mockReturnValue({ identity: null, status: 'error' });

    const { result } = renderHook(() => useSquareIdentity());

    expect(result.current.listStatus).toBe('error');
  });

  it('AC-6 页内重试：加载失败后重试成功，身份列表恢复就绪', async () => {
    useWorkspaceStore.getState().reset();
    mockedUseHumanIdentity.mockReturnValue({ identity: null, status: 'error' });
    mockedLoadIdentities.mockResolvedValue({
      ok: true,
      data: { identities: IDENTITIES, defaultActiveId: 'human_900003' },
    });

    const { result } = renderHook(() => useSquareIdentity());
    expect(result.current.listStatus).toBe('error');

    await act(async () => {
      await result.current.retryLoadIdentities();
    });

    expect(mockedLoadIdentities).toHaveBeenCalledTimes(1);
    expect(result.current.listStatus).toBe('ready');
    expect(result.current.identities).toHaveLength(3);
    expect(result.current.userIdentity).toMatchObject({ id: 'human_900003', kind: 'user' });
  });

  it('AC-6 页内重试：重试仍失败时保持 error 态，可再次重试', async () => {
    useWorkspaceStore.getState().reset();
    mockedUseHumanIdentity.mockReturnValue({ identity: null, status: 'error' });
    mockedLoadIdentities.mockResolvedValue({
      ok: false,
      error: { code: 'IDENTITY_LOAD_FAILED', friendlyMessage: '加载可协作身份失败，请稍后重试。', canRetry: true },
    });

    const { result } = renderHook(() => useSquareIdentity());

    await act(async () => {
      await result.current.retryLoadIdentities();
    });

    expect(result.current.listStatus).toBe('error');
    expect(typeof result.current.retryLoadIdentities).toBe('function');
  });
});
