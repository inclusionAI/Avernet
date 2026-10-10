/** @jest-environment jsdom */
import type { ConversationRouteState } from '@/domain/conversation';
import { useConversationUrlSync } from '@/pages/Workspace/Chat/hooks/useConversationUrlSync';
import { renderHook } from '@testing-library/react';
import { history, useLocation } from '@umijs/max';

jest.mock('@umijs/max', () => ({
  history: { replace: require('jest-mock').fn() },
  useLocation: require('jest-mock').fn(),
}));

const historyReplace = history.replace as jest.MockedFunction<typeof history.replace>;
const useLocationMock = useLocation as jest.MockedFunction<typeof useLocation>;

function mockLocation() {
  useLocationMock.mockImplementation(() => ({
    pathname: window.location.pathname,
    search: window.location.search,
    hash: window.location.hash,
    state: undefined,
    key: 'default',
  }));
}

function atUrl(url: string) {
  window.history.replaceState({}, '', url);
}

const NO_SELECTION: ConversationRouteState = {};

beforeEach(() => {
  historyReplace.mockReset();
  useLocationMock.mockReset();
  atUrl('/workspace/chat');
  mockLocation();
});

describe('useConversationUrlSync', () => {
  it('parses the route on mount and forwards it to onRouteSelection', () => {
    atUrl('/workspace/chat?section=managed&bot=b-1&origin=mine&session=s1');
    const onRouteSelection = jest.fn();

    renderHook(() => useConversationUrlSync({ hydrated: false, selection: {}, onRouteSelection }));

    expect(onRouteSelection).toHaveBeenCalledWith({
      section: 'managed',
      botId: 'b-1',
      origin: 'mine',
      scope: undefined,
      friendUserId: undefined,
      sessionId: 's1',
    });
  });

  it('forwards an unresolved section as-is instead of guessing', () => {
    atUrl('/workspace/chat?bot=old-bot&session=s9');
    const onRouteSelection = jest.fn();

    renderHook(() => useConversationUrlSync({ hydrated: false, selection: {}, onRouteSelection }));

    expect(onRouteSelection).toHaveBeenCalledWith({ botId: 'old-bot', sessionId: 's9' });
    expect(onRouteSelection.mock.calls[0][0]).not.toHaveProperty('section', 'managed');
  });

  it('re-parses on browser navigation', () => {
    atUrl('/workspace/chat?section=managed&bot=b-1');
    const onRouteSelection = jest.fn();
    const { rerender } = renderHook(() => useConversationUrlSync({ hydrated: false, selection: {}, onRouteSelection }));
    expect(onRouteSelection).toHaveBeenCalledTimes(1);

    atUrl('/workspace/chat?section=friend&bot=fb-1');
    rerender();

    expect(onRouteSelection).toHaveBeenCalledTimes(2);
    expect(onRouteSelection).toHaveBeenLastCalledWith({ section: 'friend', botId: 'fb-1' });
  });

  it('does not project the selection before hydration', () => {
    const selection: ConversationRouteState = {
      section: 'managed',
      botId: 'b-1',
      origin: 'mine',
      scope: 'favorite',
    };
    const { rerender } = renderHook(
      (props: { hydrated: boolean }) => useConversationUrlSync({ ...props, selection, onRouteSelection: jest.fn() }),
      { initialProps: { hydrated: false } },
    );

    rerender({ hydrated: false });

    expect(historyReplace).not.toHaveBeenCalled();
  });

  it('projects the canonical selection after hydration', () => {
    atUrl('/workspace/chat?old=1');
    renderHook(() =>
      useConversationUrlSync({
        hydrated: true,
        selection: { section: 'managed', botId: 'b-1', origin: 'mine', scope: 'favorite' },
        onRouteSelection: jest.fn(),
      }),
    );

    expect(historyReplace).toHaveBeenCalledWith('/workspace/chat?section=managed&bot=b-1&origin=mine&scope=favorite');
  });

  it('projects the current selection only and never current/tab', () => {
    atUrl('/workspace/chat?current=327325&legacy=1');
    renderHook(() =>
      useConversationUrlSync({
        hydrated: true,
        selection: { section: 'managed', botId: 'b-1', origin: 'others', friendUserId: '447147', sessionId: 's1' },
        onRouteSelection: jest.fn(),
      }),
    );

    const next = historyReplace.mock.calls[0][0];
    expect(next).toBe('/workspace/chat?section=managed&bot=b-1&origin=others&friend=447147&session=s1');
    expect(next).not.toContain('current=');
    expect(next).not.toContain('tab=');
  });

  it('skips the projection when the URL is already canonical', () => {
    atUrl('/workspace/chat?section=managed&bot=b-1&origin=mine&scope=favorite');
    renderHook(() =>
      useConversationUrlSync({
        hydrated: true,
        selection: { section: 'managed', botId: 'b-1', origin: 'mine', scope: 'favorite' },
        onRouteSelection: jest.fn(),
      }),
    );

    expect(historyReplace).not.toHaveBeenCalled();
  });

  it('starts projecting once hydration flips to true', () => {
    atUrl('/workspace/chat');
    const { rerender } = renderHook(
      (props: { hydrated: boolean }) =>
        useConversationUrlSync({
          ...props,
          selection: NO_SELECTION,
          onRouteSelection: jest.fn(),
        }),
      { initialProps: { hydrated: false } },
    );
    expect(historyReplace).not.toHaveBeenCalled();

    atUrl('/workspace/chat?stale=1');
    rerender({ hydrated: true });

    expect(historyReplace).toHaveBeenCalledWith('/workspace/chat');
  });

  it('does not re-feed its own projection back as a route selection', () => {
    atUrl('/workspace/chat?old=1');
    const onRouteSelection = jest.fn();
    const selection: ConversationRouteState = { section: 'managed', botId: 'b-1', origin: 'mine' };
    const { rerender } = renderHook(
      (props: { selection: ConversationRouteState }) =>
        useConversationUrlSync({ hydrated: true, ...props, onRouteSelection }),
      { initialProps: { selection } },
    );
    // 挂载时先解析 ?old=1，随后写回 canonical URL。
    expect(onRouteSelection).toHaveBeenCalledTimes(1);
    expect(historyReplace).toHaveBeenCalledWith('/workspace/chat?section=managed&bot=b-1&origin=mine');

    // 我们自己写回的 canonical URL 同步进 location 后不得再次触发路由分发。
    atUrl('/workspace/chat?section=managed&bot=b-1&origin=mine');
    rerender({ selection: { section: 'managed', botId: 'b-1', origin: 'mine' } });

    expect(onRouteSelection).toHaveBeenCalledTimes(1);
    expect(historyReplace).toHaveBeenCalledTimes(1);
  });
});
