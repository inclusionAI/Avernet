/** @jest-environment jsdom */
// Former observer route/DOM assertions now exercise the real host adapter, never old store nav commands.
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import { navigateTaskPanelConversation, captureTaskPanelOrigin, restoreTaskPanelOrigin } from '@/services/tasks/panel/taskPanelConversationAdapter';
import { markTaskNavigationRouteHydrated } from '@/services/tasks/taskConversationNavigation';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { TaskNavTarget } from '@/stores/taskSidePanelStoreTypes';

const mockNavigate = jest.fn<(target: TaskNavTarget) => { status: string; value: null; reason?: string }>();
jest.mock('@/capabilities', () => ({ getCapabilities: () => ({ navigateToConversation: (...args: [TaskNavTarget]) => mockNavigate(...args) }) }));
const target: TaskNavTarget = { sessionId: 'agent:main:session:s-1', kind: 'bot', botId: 'bot-9' };
const originUrl = '/workspace/chat?bot=bot-host&session=s-host';
function hydrate(sessionId = target.sessionId, pathname: '/workspace/chat' | '/workspace/collaboration' = '/workspace/chat') {
  window.history.pushState({}, '', `${pathname}?bot=bot-9&session=${encodeURIComponent(sessionId)}`);
  markTaskNavigationRouteHydrated(sessionId, pathname);
}
function message(timestamp = 1700000003000) {
  const el = document.createElement('div'); el.setAttribute('data-ts', String(timestamp));
  el.scrollIntoView = jest.fn(); document.body.appendChild(el); return el;
}
const windowTarget = { ...target, startTime: 1700000000000, endTime: 1700000005000 };

beforeEach(() => {
  jest.useFakeTimers(); jest.clearAllMocks(); document.body.innerHTML = '';
  useTaskSidePanelStore.getState().resetAll(); useTaskConversationNavigationStore.getState().resetAll();
  useTaskSidePanelStore.setState({ activeTaskId: 't1' }); // output-only projection fixture
  window.history.pushState({}, '', originUrl);
  mockNavigate.mockReturnValue({ status: 'available', value: null });
});
afterEach(() => { jest.clearAllTimers(); jest.useRealTimers(); });

describe('host route and readiness seam (migrated TaskSessionNavigationObserver)', () => {
  it('captures the exact original route, forwards group/bot/time fields and establishes the lease before capability invocation', async () => {
    expect(captureTaskPanelOrigin()).toEqual({ routeUrl: originUrl, sessionId: 's-host', kind: 'bot', botId: 'bot-host' });
    const group: TaskNavTarget = { sessionId: 'bcs_grp_902:round:2', kind: 'group', groupId: 'g-902', botId: null,
      startTime: 1700000000000, endTime: 1700000005000 };
    mockNavigate.mockImplementation((value: TaskNavTarget) => {
      expect(value).toEqual(group);
      expect(useTaskConversationNavigationStore.getState().navigation).toMatchObject({
        taskId: 't1', pathname: '/workspace/collaboration', sessionId: group.sessionId, routeCommitted: true, routeHydrated: false,
      });
      hydrate(group.sessionId, '/workspace/collaboration'); return { status: 'available', value: null };
    });
    const el = message();
    await expect(navigateTaskPanelConversation(group, new AbortController().signal)).resolves.toEqual({ status: 'ready' });
    expect(mockNavigate).toHaveBeenCalledTimes(1); expect(el.scrollIntoView).toHaveBeenCalledTimes(1);
  });
  it('forwards botId and waits for destination hydration, not just route commit', async () => {
    const pending = navigateTaskPanelConversation(target, new AbortController().signal);
    expect(mockNavigate).toHaveBeenCalledWith(target);
    expect(useTaskConversationNavigationStore.getState().navigation?.routeHydrated).toBe(false);
    hydrate(); await jest.advanceTimersByTimeAsync(100);
    await expect(pending).resolves.toEqual({ status: 'ready' }); expect(jest.getTimerCount()).toBe(0);
  });
  it('same-current-conversation no-op completes hydration without any route lifecycle event', async () => {
    const pending = navigateTaskPanelConversation({ sessionId: 's-host', kind: 'bot', botId: 'bot-host' }, new AbortController().signal);
    await jest.advanceTimersByTimeAsync(100); await expect(pending).resolves.toEqual({ status: 'ready' });
    expect(useTaskConversationNavigationStore.getState().navigation).toMatchObject({ routeCommitted: true, routeHydrated: true });
    expect(jest.getTimerCount()).toBe(0);
  });
  it('restores an exact opaque origin after a linked group route, without overwriting its recorded URL', async () => {
    const origin = captureTaskPanelOrigin(); window.history.pushState({}, '', '/workspace/collaboration?group=g2&session=s-linked');
    mockNavigate.mockImplementation((value: TaskNavTarget) => {
      expect(value).toEqual(origin); window.history.pushState({}, '', value.routeUrl!);
      markTaskNavigationRouteHydrated(value.sessionId, '/workspace/chat'); return { status: 'available', value: null };
    });
    await expect(restoreTaskPanelOrigin(origin, new AbortController().signal)).resolves.toEqual({ status: 'ready' });
    expect(origin?.routeUrl).toBe(originUrl); expect(mockNavigate).toHaveBeenCalledTimes(1);
  });
  it('semantic same-session origin restore leaves a normalized URL alone', async () => {
    const origin = captureTaskPanelOrigin();
    const normalized = '/workspace/chat?section=managed&bot=bot-host&session=s-host#latest';
    window.history.pushState({}, '', normalized);
    await expect(restoreTaskPanelOrigin(origin, new AbortController().signal)).resolves.toEqual({ status: 'ready' });
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(window.location.pathname + window.location.search + window.location.hash).toBe(normalized);
  });
  it('unavailable capability returns an actionable outcome and releases the lease, never retries the command', async () => {
    mockNavigate.mockReturnValue({ status: 'unsupported', value: null, reason: '不支持' });
    await expect(navigateTaskPanelConversation(target, new AbortController().signal)).resolves.toEqual({ status: 'unavailable', reason: '不支持' });
    expect(useTaskConversationNavigationStore.getState().navigation).toBeNull(); expect(mockNavigate).toHaveBeenCalledTimes(1);
    expect(jest.getTimerCount()).toBe(0);
  });
  it('timeout releases a route lease rather than preserving a never-ready panel indefinitely', async () => {
    const pending = navigateTaskPanelConversation(target, new AbortController().signal);
    await jest.advanceTimersByTimeAsync(10_000);
    await expect(pending).resolves.toMatchObject({ status: 'unavailable', reason: expect.stringContaining('超时') });
    expect(useTaskConversationNavigationStore.getState().navigation).toBeNull();
  });
  it('abort cancels waiting and prevents later DOM scroll', async () => {
    const abort = new AbortController(); const pending = navigateTaskPanelConversation(windowTarget, abort.signal).catch(error => error);
    abort.abort(); const el = message(); hydrate(); await jest.advanceTimersByTimeAsync(100);
    expect((await pending).name).toBe('AbortError'); expect(el.scrollIntoView).not.toHaveBeenCalled();
    expect(useTaskConversationNavigationStore.getState().navigation).toBeNull();
  });
});

describe('message-time-window DOM location in the actual host adapter', () => {
  beforeEach(() => mockNavigate.mockImplementation((value: TaskNavTarget) => {
    hydrate(value.sessionId); return { status: 'available', value: null };
  }));
  it('scrolls a real data-ts message in the node time window after hydration', async () => {
    const el = message();
    await expect(navigateTaskPanelConversation(windowTarget, new AbortController().signal)).resolves.toEqual({ status: 'ready' });
    expect(el.scrollIntoView).toHaveBeenCalledTimes(1);
  });
  it('missing DOM message degrades to a ready conversation without throwing', async () => {
    const pending = navigateTaskPanelConversation(windowTarget, new AbortController().signal);
    await jest.advanceTimersByTimeAsync(5_000);
    await expect(pending).resolves.toEqual({ status: 'ready' });
  });
  it('without a time window, does not scroll or keep probe timers after readiness', async () => {
    const el = message();
    await expect(navigateTaskPanelConversation(target, new AbortController().signal)).resolves.toEqual({ status: 'ready' });
    expect(el.scrollIntoView).not.toHaveBeenCalled(); expect(jest.getTimerCount()).toBe(0);
  });
  it('locates a message mounted after hydration using a bounded retry', async () => {
    mockNavigate.mockImplementation(() => { hydrate(); return { status: 'available', value: null }; });
    const pending = navigateTaskPanelConversation(windowTarget, new AbortController().signal);
    await jest.advanceTimersByTimeAsync(900); const el = message(1700000001000);
    await jest.advanceTimersByTimeAsync(500);
    await expect(pending).resolves.toEqual({ status: 'ready' });
    expect(el.scrollIntoView).toHaveBeenCalledTimes(1); expect(jest.getTimerCount()).toBe(0);
  });
  it('stops delayed DOM probing immediately on cancellation', async () => {
    mockNavigate.mockImplementation(() => { hydrate(); return { status: 'available', value: null }; });
    const abort = new AbortController();
    const pending = navigateTaskPanelConversation(windowTarget, abort.signal).catch(error => error);
    await jest.advanceTimersByTimeAsync(500); abort.abort();
    const el = message();
    expect((await pending).name).toBe('AbortError');
    expect(el.scrollIntoView).not.toHaveBeenCalled(); expect(jest.getTimerCount()).toBe(0);
  });
  it('bounds DOM retries and degrades to conversation-only navigation without leaking timers', async () => {
    mockNavigate.mockImplementation(() => { hydrate(); return { status: 'available', value: null }; });
    const pending = navigateTaskPanelConversation(windowTarget, new AbortController().signal);
    await jest.advanceTimersByTimeAsync(5_000);
    await expect(pending).resolves.toEqual({ status: 'ready' });
    expect(jest.getTimerCount()).toBe(0);
  });
  it('releases its lease if the host navigation capability throws', async () => {
    mockNavigate.mockImplementation(() => { throw new Error('route failed'); });
    await expect(navigateTaskPanelConversation(target, new AbortController().signal)).rejects.toThrow('route failed');
    expect(useTaskConversationNavigationStore.getState().navigation).toBeNull();
  });
});
