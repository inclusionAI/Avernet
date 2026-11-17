/** @jest-environment jsdom */
import { useWorkspacePage } from '@/pages/Workspace/hooks/useWorkspacePage';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history, useSearchParams } from '@umijs/max';
import { act, renderHook } from '@testing-library/react';
import { baseGroup } from '../components/GroupSidebar/GroupSidebar.testUtils';

jest.mock('@umijs/max', () => ({ history: { replace: jest.fn() }, useSearchParams: jest.fn() }));
jest.mock('@/services/workspace/sessionService');
const human = { id: 'human_staff-1', kind: 'user' as const, displayName: '我', online: true };
const bot = { id: 'bot-1', kind: 'bot' as const, displayName: 'Bot', online: true };
const deepLink = '/workspace/collaboration?current=human_staff-1&group=g1&session=s1';

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities([human, bot], human.id);
  window.history.replaceState({}, '', deepLink);
  jest.mocked(useSearchParams).mockImplementation(() => [new URLSearchParams(window.location.search), jest.fn()]);
  jest.mocked(history.replace).mockImplementation((path) => { window.history.replaceState({}, '', String(path)); });
});

it('issues one location request for an explicit session deep link, not again for its canonical projection or identity-list refresh', async () => {
  const { result, rerender } = renderHook(() => useWorkspacePage());
  await act(async () => {});
  expect(result.current.sessionLocationRequest).toMatchObject({ identityId: human.id, groupId: 'g1', sessionId: 's1' });
  const request = result.current.sessionLocationRequest?.request;
  rerender();
  act(() => useWorkspaceStore.getState().setIdentities([human, bot], human.id));
  expect(result.current.sessionLocationRequest?.request).toBe(request);
});

it('does not turn an internal session click into a new deep-link location request', async () => {
  const { result, rerender } = renderHook(() => useWorkspacePage());
  await act(async () => {});
  rerender();
  act(() => useWorkspaceStore.getState().selectSession('s2'));
  expect(window.location.search).toContain('session=s2');
  rerender();
  expect(result.current.sessionLocationRequest).toBeUndefined();
});

it('issues a fresh request on query navigation and browser back to an earlier target', async () => {
  const { result, rerender } = renderHook(() => useWorkspacePage());
  await act(async () => {});
  const first = result.current.sessionLocationRequest?.request;
  window.history.replaceState({}, '', deepLink.replace('s1', 's2'));
  rerender();
  expect(result.current.sessionLocationRequest).toMatchObject({ sessionId: 's2' });
  expect(result.current.sessionLocationRequest!.request).toBeGreaterThan(first!);
  window.history.replaceState({}, '', deepLink);
  rerender();
  expect(result.current.sessionLocationRequest).toMatchObject({ sessionId: 's1' });
  expect(result.current.sessionLocationRequest!.request).toBeGreaterThan(first!);
});

it('keeps the requested Human in the URL until identities contain that user', async () => {
  useWorkspaceStore.getState().setIdentities([bot], bot.id);
  const { result } = renderHook(() => useWorkspacePage());
  await act(async () => {});
  expect(result.current.urlHydrated).toBe(false);
  expect(result.current.sessionLocationRequest).toBeUndefined();
  expect(history.replace).not.toHaveBeenCalled();
  expect(window.location.search).toContain('current=human_staff-1');
  act(() => useWorkspaceStore.getState().setIdentities([human, bot], bot.id));
  expect(result.current.urlHydrated).toBe(true);
  expect(useWorkspaceStore.getState().activeIdentityId).toBe(human.id);
  expect(result.current.sessionLocationRequest).toMatchObject({ identityId: human.id, sessionId: 's1' });
});

it('does not request automatic location for a bare URL restoring remembered selection', async () => {
  window.history.replaceState({}, '', '/workspace/collaboration');
  useWorkspaceStore.setState({ selectedGroupId: 'g1', selectedSessionId: 's1', view: 'group' });
  const { result, rerender } = renderHook(() => useWorkspacePage());
  await act(async () => {});
  rerender();
  expect(result.current.sessionLocationRequest).toBeUndefined();
});


it('waits for session-only URL detail resolution before publishing its parent-group location', async () => {
  window.history.replaceState({}, '', '/workspace/collaboration?current=human_staff-1&session=s1');
  let resolve!: (value: Awaited<ReturnType<typeof sessionService.getSessionDetail>>) => void;
  jest.mocked(sessionService.getSessionDetail).mockReturnValueOnce(new Promise((r) => { resolve = r; }));
  const { result } = renderHook(() => useWorkspacePage());
  expect(result.current.urlHydrated).toBe(false);
  expect(result.current.sessionLocationRequest).toBeUndefined();
  await act(async () => resolve({ ok: true, data: baseGroup.sessions[0] }));
  expect(result.current.sessionLocationRequest).toMatchObject({ identityId: human.id, groupId: 'g1', sessionId: 's1' });
});

it('does not let an old session-detail response replace a newer URL target', async () => {
  window.history.replaceState({}, '', '/workspace/collaboration?current=human_staff-1&session=s1');
  let resolve!: (value: Awaited<ReturnType<typeof sessionService.getSessionDetail>>) => void;
  jest.mocked(sessionService.getSessionDetail).mockReturnValueOnce(new Promise((r) => { resolve = r; }));
  const { result, rerender } = renderHook(() => useWorkspacePage());
  window.history.replaceState({}, '', '/workspace/collaboration?current=human_staff-1&group=g2&session=s2');
  rerender();
  const request = result.current.sessionLocationRequest?.request;
  await act(async () => resolve({ ok: true, data: baseGroup.sessions[0] }));
  expect(result.current.sessionLocationRequest).toMatchObject({ request, groupId: 'g2', sessionId: 's2' });
  expect(useWorkspaceStore.getState()).toMatchObject({ selectedGroupId: 'g2', selectedSessionId: 's2' });
});
