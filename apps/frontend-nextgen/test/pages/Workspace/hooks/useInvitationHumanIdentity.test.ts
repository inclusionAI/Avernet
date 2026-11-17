/** @jest-environment jsdom */
import { getCapabilities } from '@/capabilities';
import { useInvitationHumanIdentity } from '@/pages/Workspace/hooks/useInvitationHumanIdentity';
import { identityService } from '@/services/workspace/identityService';
import { listMyBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/services/workspace/identityService');
jest.mock('@/services/backendApi/collaboration/collaborationBotController');

const human = { userId: 'staff-1', displayName: '我', online: true };

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  useWorkspaceStore.getState().reset();
  useExternalAuthStore.getState().reset();
  jest.spyOn(getCapabilities(), 'getLoginStrategy').mockReturnValue({ status: 'available', value: 'ace-gateway' });
  jest.spyOn(getCapabilities(), 'getHumanIdentity').mockReturnValue({ status: 'available', value: null });
});

afterEach(() => {
  jest.clearAllTimers();
  jest.useRealTimers();
  jest.restoreAllMocks();
});

it('treats a cold gateway identity as initializing until the global store updates', () => {
  const { result, unmount } = renderHook(() => useInvitationHumanIdentity());
  expect(result.current).toMatchObject({ status: 'loading', userId: null });
  jest.mocked(getCapabilities().getHumanIdentity).mockReturnValue({ status: 'available', value: human });
  act(() => useWorkspaceStore.getState().setIdentities([
    { id: 'human_staff-1', kind: 'user', displayName: '我', online: true },
  ], 'human_staff-1'));
  expect(result.current).toMatchObject({ status: 'ready', userId: 'staff-1' });
  expect(jest.getTimerCount()).toBe(0);
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  unmount();
});

it('observes late synchronous capability initialization even without a store notification', () => {
  const { result, unmount } = renderHook(() => useInvitationHumanIdentity());
  expect(result.current.status).toBe('loading');
  act(() => jest.advanceTimersByTime(800));
  expect(result.current.status).toBe('loading');
  jest.mocked(getCapabilities().getHumanIdentity).mockReturnValue({ status: 'available', value: human });
  act(() => jest.advanceTimersByTime(200));
  expect(result.current).toMatchObject({ status: 'ready', userId: 'staff-1' });
  expect(jest.getTimerCount()).toBe(0);
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  unmount();
});

it.each(['ace-gateway', 'oauth-provider'] as const)('bounds unresolved %s initialization to ten seconds', (strategy) => {
  jest.mocked(getCapabilities().getLoginStrategy).mockReturnValue({ status: 'available', value: strategy });
  const { result, unmount } = renderHook(() => useInvitationHumanIdentity());
  act(() => jest.advanceTimersByTime(9_800));
  expect(result.current.status).toBe('loading');
  act(() => jest.advanceTimersByTime(200));
  expect(result.current).toMatchObject({ status: 'error', userId: null });
  expect(result.current.error).toContain('超时');
  expect(jest.getTimerCount()).toBe(0);
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  unmount();
});

it.each(['unauthenticated', 'error'] as const)('does not delay an explicit OAuth %s failure', (status) => {
  jest.mocked(getCapabilities().getLoginStrategy).mockReturnValue({ status: 'available', value: 'oauth-provider' });
  useExternalAuthStore.setState({ status });
  const { result, unmount } = renderHook(() => useInvitationHumanIdentity());
  expect(result.current.status).toBe('error');
  expect(jest.getTimerCount()).toBe(0);
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  unmount();
});

it('does not wait on an explicitly unsupported identity capability', () => {
  jest.mocked(getCapabilities().getHumanIdentity).mockReturnValue({ status: 'unsupported', value: null });
  const { result, unmount } = renderHook(() => useInvitationHumanIdentity());
  expect(result.current.status).toBe('error');
  expect(jest.getTimerCount()).toBe(0);
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  unmount();
});

it('cleans up initialization observation on unmount', () => {
  const { unmount } = renderHook(() => useInvitationHumanIdentity());
  expect(jest.getTimerCount()).toBeGreaterThan(0);
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  unmount();
  expect(jest.getTimerCount()).toBe(0);
});

it('gives a subsequent initialization its own deadline and accepts a late authenticated User', () => {
  jest.mocked(getCapabilities().getLoginStrategy).mockReturnValue({ status: 'available', value: 'oauth-provider' });
  const { result, unmount } = renderHook(() => useInvitationHumanIdentity());
  act(() => jest.advanceTimersByTime(10_000));
  expect(result.current.status).toBe('error');
  jest.mocked(getCapabilities().getHumanIdentity).mockReturnValue({ status: 'available', value: human });
  act(() => useExternalAuthStore.getState().setAuthenticated({ ...human, provider: 'github' }));
  expect(result.current.status).toBe('ready');
  act(() => useExternalAuthStore.getState().reset());
  expect(result.current.status).toBe('loading');
  act(() => jest.advanceTimersByTime(9_800));
  expect(result.current.status).toBe('loading');
  act(() => useExternalAuthStore.getState().setAuthenticated({ ...human, provider: 'github' }));
  expect(result.current).toMatchObject({ status: 'ready', userId: 'staff-1' });
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  unmount();
});
