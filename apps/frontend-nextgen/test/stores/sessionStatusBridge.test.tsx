/** @jest-environment jsdom */
import type { SessionStatusEvent } from '@/domain/conversation/sessionStatusProtocol';
import { createSessionStatusReceiver, isSessionStatusEvent } from '@/domain/conversation/sessionStatusReceiver';
import { ConversationSessionStatus } from '@/pages/Workspace/Chat/components/ConversationSessionStatus';
import { useSessionStatusBridge } from '@/pages/Workspace/Chat/hooks/useSessionStatusBridge';
import { useConversationStore } from '@/stores/conversationStore';
import { useSessionStatusStore } from '@/stores/sessionStatusStore';
import { act, render, renderHook, screen } from '@testing-library/react';

import '@testing-library/jest-dom';

const status = { code: 'running', label: '进行中', tone: 'info' as const };
const sid = 'user:u-1:session:session-1:agent:bot-a';
const patch = (contextId = 'ctx', sequence = 1): SessionStatusEvent => ({
  version: 1,
  contextId,
  botId: 'bot-a',
  sequence,
  type: 'patch',
  items: [{ sessionId: sid, status }],
});
beforeEach(() => {
  useConversationStore.getState().reset();
  useSessionStatusStore.setState({
    contextId: null,
    botId: null,
    ready: false,
    snapshotReady: false,
    statuses: {},
  });
  useSessionStatusStore.getState().begin('ctx', 'bot-a:owner');
  useSessionStatusStore.getState().patch('ctx', [], new Set(), { snapshot: true });
  useSessionStatusStore.getState().commit('ctx');
});

afterEach(() => {
  useConversationStore.getState().reset();
  useSessionStatusStore.setState({
    contextId: null,
    botId: null,
    ready: false,
    snapshotReady: false,
    statuses: {},
  });
});
it('validates version, sequence, payload and tone before accepting events', () => {
  expect(isSessionStatusEvent(patch())).toBe(true);
  for (const bad of [
    null,
    {},
    { ...patch(), version: 2 },
    { ...patch(), sequence: NaN },
    { ...patch(), items: [null] },
    { ...patch(), items: [{ sessionId: sid, status: { ...status, tone: 'html' } }] },
    { ...patch(), snapshot: 'true' },
  ]) {
    expect(isSessionStatusEvent(bad)).toBe(false);
  }
});
it('rejects old contexts, duplicate/out-of-order responses and disposed subscriptions', () => {
  const apply = jest.fn();
  const receiver = createSessionStatusReceiver('ctx', 'bot-a', apply);
  receiver.receive(patch());
  receiver.activate();
  receiver.receive(patch('old'));
  receiver.receive({ ...patch(), botId: 'bot-b' });
  receiver.receive(patch('ctx', 2));
  receiver.receive(patch('ctx', 1));
  receiver.receive(patch('ctx', 2));
  expect(apply).toHaveBeenCalledTimes(1);
  receiver.dispose();
  receiver.receive(patch('ctx', 3));
  expect(apply).toHaveBeenCalledTimes(1);
});
it('patches known rows only, retains omitted values, deduplicates, clears explicitly and prunes deletion', () => {
  const store = useSessionStatusStore.getState();
  const known = new Set([sid, 'other']);
  store.patch(
    'ctx',
    [
      { sessionId: sid, status },
      { sessionId: 'missing', status },
    ],
    known,
  );
  const previous = useSessionStatusStore.getState().statuses;
  store.patch('ctx', [{ sessionId: sid, status: { ...status } }], known);
  expect(useSessionStatusStore.getState().statuses).toBe(previous);
  store.patch('ctx', [], known);
  expect(useSessionStatusStore.getState().statuses[sid]).toEqual(status);
  store.patch('ctx', [{ sessionId: 'other', status }], known);
  store.patch('ctx', [{ sessionId: sid, status: null }], known);
  expect(useSessionStatusStore.getState().statuses).toEqual({ other: status });
  store.patch('ctx', [{ sessionId: sid, status }], new Set());
  expect(useSessionStatusStore.getState().statuses).toEqual({});
});
it('isolates view identity, gives normal rows the default conversation icon, and uses accessible text', () => {
  useSessionStatusStore.getState().patch('ctx', [{ sessionId: sid, status }], new Set([sid]));
  const view = render(
    <>
      <ConversationSessionStatus botId="bot-a:owner" sessionId={sid} />
      <ConversationSessionStatus botId="bot-a:other-owner" sessionId={sid} />
    </>,
  );
  expect(screen.getAllByLabelText('DevFlow：进行中')).toHaveLength(1);
  const defaultIcons = view.container.querySelectorAll('svg.lucide-message-square');
  expect(defaultIcons).toHaveLength(1);
  // The neutral placeholder stays subtle and does not compete with DevFlow states.
  expect(defaultIcons[0]).toHaveClass('text-content-icon');
  act(() => useSessionStatusStore.getState().end('ctx'));
  expect(screen.queryByLabelText(/DevFlow/)).toBeNull();
  expect(view.container.querySelectorAll('svg.lucide-message-square')).toHaveLength(2);
});

it('buffers the initial snapshot before surface ready and swaps all rows to DevFlow icons together', () => {
  const other = 'user:u-1:session:session-2:agent:bot-a:owner';
  useSessionStatusStore.getState().begin('ctx', 'bot-a:owner');
  useSessionStatusStore.getState().patch(
    'ctx',
    [
      { sessionId: sid, status },
      { sessionId: other, status: { ...status, code: 'completed', label: '已完成', tone: 'success' } },
    ],
    new Set([sid, other]),
    { snapshot: true },
  );
  const view = render(
    <>
      <ConversationSessionStatus botId="bot-a:owner" sessionId={sid} />
      <ConversationSessionStatus botId="bot-a:owner" sessionId={other} />
    </>,
  );
  expect(screen.queryByLabelText(/DevFlow/)).toBeNull();
  expect(view.container.querySelectorAll('svg.lucide-message-square')).toHaveLength(2);

  act(() => useSessionStatusStore.getState().commit('ctx'));
  expect(screen.getAllByLabelText('DevFlow：进行中')).toHaveLength(1);
  expect(screen.getAllByLabelText('DevFlow：已完成')).toHaveLength(1);
  expect(view.container.querySelector('svg.lucide-message-square')).toBeNull();
});

it('waits for both surface readiness and the initial status snapshot before deviating from the default icon', () => {
  useSessionStatusStore.getState().begin('ctx', 'bot-a:owner');
  useSessionStatusStore.getState().patch('ctx', [{ sessionId: sid, status }], new Set([sid]));
  const view = render(<ConversationSessionStatus botId="bot-a:owner" sessionId={sid} />);

  act(() => useSessionStatusStore.getState().commit('ctx'));
  expect(screen.queryByLabelText(/DevFlow/)).toBeNull();
  expect(view.container.querySelector('svg.lucide-message-square')).toBeTruthy();

  act(() =>
    useSessionStatusStore.getState().patch('ctx', [{ sessionId: sid, status }], new Set([sid]), { snapshot: true }),
  );
  expect(screen.getByLabelText('DevFlow：进行中')).toBeTruthy();
  expect(view.container.querySelector('svg.lucide-message-square')).toBeNull();
});
it('treats the first normal patch from an older child as the initial snapshot', () => {
  const row = { sessionId: sid, botId: 'bot-a:owner', title: 'Title', messageCount: 1, gmtModified: '', gmtCreate: '' };
  const list = {
    items: [row],
    page: 1,
    total: 1,
    hasMore: false,
    loading: false,
    error: null,
    isLoadingMore: false,
    loadMoreError: null,
  };
  useConversationStore.getState().setFriendBotSessions('bot-a:owner', list);
  const { result } = renderHook(() => useSessionStatusBridge('bot-a:owner', 'bot-a', 'user-1:retry-0'));
  const view = render(<ConversationSessionStatus botId="bot-a:owner" sessionId={sid} />);
  const contextId = result.current.sessionStatusContextId;

  act(() => result.current.onSessionStatusEvent(patch(contextId, 1)));
  expect(useSessionStatusStore.getState().snapshotReady).toBe(true);
  expect(screen.queryByLabelText(/DevFlow/)).toBeNull();

  act(() => result.current.commitSessionStatuses());
  expect(screen.getByLabelText('DevFlow：进行中')).toBeTruthy();
  view.unmount();
});

it('keeps an explicit v1 regular patch from satisfying the initial snapshot gate', () => {
  const row = { sessionId: sid, botId: 'bot-a:owner', title: 'Title', messageCount: 1, gmtModified: '', gmtCreate: '' };
  const list = {
    items: [row],
    page: 1,
    total: 1,
    hasMore: false,
    loading: false,
    error: null,
    isLoadingMore: false,
    loadMoreError: null,
  };
  useConversationStore.getState().setFriendBotSessions('bot-a:owner', list);
  const { result } = renderHook(() => useSessionStatusBridge('bot-a:owner', 'bot-a', 'user-1:retry-0'));
  const view = render(<ConversationSessionStatus botId="bot-a:owner" sessionId={sid} />);
  const contextId = result.current.sessionStatusContextId;

  act(() => {
    result.current.onSessionStatusEvent({ ...patch(contextId, 1), snapshot: false });
    result.current.commitSessionStatuses();
  });

  expect(useSessionStatusStore.getState().snapshotReady).toBe(false);
  expect(screen.queryByLabelText(/DevFlow/)).toBeNull();

  act(() => {
    result.current.onSessionStatusEvent({ ...patch(contextId, 2), snapshot: true });
  });
  expect(screen.getByLabelText('DevFlow：进行中')).toBeTruthy();
  view.unmount();
});

it('buffers a status snapshot that wins the host-list race, then flushes it when rows appear', () => {
  const { result, unmount } = renderHook(() => useSessionStatusBridge('bot-a:owner', 'bot-a', 'user-1'));
  const contextId = result.current.sessionStatusContextId;

  act(() => {
    result.current.onSessionStatusEvent(patch(contextId, 1));
    result.current.commitSessionStatuses();
  });
  expect(useSessionStatusStore.getState().snapshotReady).toBe(true);
  expect(useSessionStatusStore.getState().statuses[sid]).toEqual(status);

  const row = {
    sessionId: sid,
    botId: 'bot-a:owner',
    title: 'Title',
    messageCount: 1,
    gmtModified: '',
    gmtCreate: '',
  };
  act(() =>
    useConversationStore.getState().setFriendBotSessions('bot-a:owner', {
      items: [row],
      page: 1,
      total: 1,
      hasMore: false,
      loading: false,
      error: null,
      isLoadingMore: false,
      loadMoreError: null,
    }),
  );
  const view = render(<ConversationSessionStatus botId="bot-a:owner" sessionId={sid} />);
  expect(screen.getByLabelText('DevFlow：进行中')).toBeTruthy();
  view.unmount();
  unmount();
});

it('keeps context through session rerenders; switches identity, retry and A→B→A invalidate old callbacks', () => {
  const { result, rerender, unmount } = renderHook(
    ({ bot, identity }) => useSessionStatusBridge(`${bot}:owner`, bot, identity),
    { initialProps: { bot: 'bot-a', identity: 'user-1:retry-0' } },
  );
  const first = result.current;
  rerender({ bot: 'bot-a', identity: 'user-1:retry-0' });
  expect(result.current.sessionStatusContextId).toBe(first.sessionStatusContextId);
  rerender({ bot: 'bot-b', identity: 'user-1:retry-0' });
  rerender({ bot: 'bot-a', identity: 'user-1:retry-0' });
  expect(result.current.sessionStatusContextId).not.toBe(first.sessionStatusContextId);
  first.onSessionStatusEvent(patch(first.sessionStatusContextId));
  expect(useSessionStatusStore.getState().statuses).toEqual({});
  const beforeRetry = result.current;
  rerender({ bot: 'bot-a', identity: 'user-1:retry-1' });
  expect(result.current.sessionStatusContextId).not.toBe(beforeRetry.sessionStatusContextId);
  unmount();
  expect(useSessionStatusStore.getState().contextId).toBeNull();
});
it('bridge applies to the originating Bot while another Bot is expanded; refetch keeps status and deletion prunes it', () => {
  const row = { sessionId: sid, botId: 'bot-a:owner', title: 'Title', messageCount: 1, gmtModified: '', gmtCreate: '' };
  const list = {
    items: [row],
    page: 1,
    total: 1,
    hasMore: false,
    loading: false,
    error: null,
    isLoadingMore: false,
    loadMoreError: null,
  };
  useConversationStore.getState().setFriendBotSessions('bot-a:owner', list);
  const { result, unmount } = renderHook(() => useSessionStatusBridge('bot-a:owner', 'bot-a', 'user-1'));
  act(() => {
    useConversationStore.getState().setExpandedBot('bot-b:owner', true);
    result.current.onSessionStatusEvent(patch(result.current.sessionStatusContextId));
  });
  expect(useSessionStatusStore.getState().botId).toBe('bot-a:owner');
  expect(useSessionStatusStore.getState().statuses[sid]).toEqual(status);
  act(() =>
    useConversationStore
      .getState()
      .setFriendBotSessions('bot-a:owner', { ...list, items: [{ ...row, title: 'Renamed' }] }),
  );
  expect(useSessionStatusStore.getState().statuses[sid]).toEqual(status);
  expect(useConversationStore.getState().friendBotSessionsByBotId['bot-a:owner'].items[0].title).toBe('Renamed');
  act(() => useConversationStore.getState().setFriendBotSessions('bot-a:owner', { ...list, items: [] }));
  act(() => result.current.onSessionStatusEvent(patch(result.current.sessionStatusContextId, 2)));
  expect(useSessionStatusStore.getState().statuses).toEqual({});
  expect(useConversationStore.getState().friendBotSessionsByBotId['bot-a:owner'].items).toEqual([]);
  unmount();
});
it('merges explicit idle into the neutral conversation placeholder, not a dashed circle', () => {
  useSessionStatusStore
    .getState()
    .patch('ctx', [{ sessionId: sid, status: { code: 'idle', label: '空闲', tone: 'neutral' } }], new Set([sid]));
  const { container } = render(<ConversationSessionStatus botId="bot-a:owner" sessionId={sid} />);
  expect(screen.queryByLabelText(/DevFlow/)).toBeNull();
  expect(container.querySelector('svg.lucide-circle-dashed')).toBeNull();
  const icon = container.querySelector('svg.lucide-message-square');
  expect(icon).toBeInTheDocument();
  expect(icon).toHaveClass('size-[13px]', 'text-content-icon');
});

it.each([
  ['running', '进行中', 'lucide-loader-circle', 'text-info', 'animate-spin'],
  ['waitingHuman', '待回复', 'lucide-bell-ring', 'text-warning', 'bell-wiggle'],
])('maps animated %s to the TeamClaw semantic icon visual', (code, label, iconClass, colorClass, animationClass) => {
  useSessionStatusStore
    .getState()
    .patch('ctx', [{ sessionId: sid, status: { code, label, tone: 'neutral' } }], new Set([sid]));
  const { container } = render(<ConversationSessionStatus botId="bot-a:owner" sessionId={sid} />);
  const icon = container.querySelector('svg');
  expect(screen.getByLabelText(`DevFlow：${label}`)).toBeTruthy();
  expect(icon).toHaveClass('size-3.5', iconClass, colorClass, animationClass);
});

it.each([
  ['completed', '已完成', 'lucide-message-circle-check', 'text-success'],
  ['completedFailed', '完成（失败）', 'lucide-message-circle-warning', 'text-destructive'],
  ['failed', '失败', 'lucide-message-circle-warning', 'text-destructive'],
  ['interrupted', '已中断', 'lucide-circle-pause', 'text-warning'],
  ['aborted', '已终止', 'lucide-circle-slash', 'text-muted-foreground'],
])('maps non-animated %s to the TeamClaw semantic icon visual', (code, label, iconClass, colorClass) => {
  useSessionStatusStore
    .getState()
    .patch('ctx', [{ sessionId: sid, status: { code, label, tone: 'neutral' } }], new Set([sid]));
  const { container } = render(<ConversationSessionStatus botId="bot-a:owner" sessionId={sid} />);
  const icon = container.querySelector('svg');
  expect(screen.getByLabelText(`DevFlow：${label}`)).toBeTruthy();
  expect(icon).toHaveClass('size-3.5', iconClass, colorClass);
  expect(icon).not.toHaveClass('animate-spin', 'bell-wiggle');
});

it('renders Coding status as the compact AIX-style icon and suppresses the fallback reply spinner', () => {
  useSessionStatusStore.getState().patch('ctx', [{ sessionId: sid, status }], new Set([sid]));
  const { container, rerender } = render(
    <ConversationSessionStatus botId="bot-a:owner" sessionId={sid} showReplying />,
  );
  expect(screen.getByLabelText('DevFlow：进行中')).toBeTruthy();
  expect(screen.queryByLabelText('Bot 正在回复')).toBeNull();
  expect(screen.queryByText('进行中')).toBeNull();
  expect(container.querySelector('svg')).toHaveClass('size-3.5', 'animate-spin', 'text-info');

  act(() => useSessionStatusStore.getState().patch('ctx', [{ sessionId: sid, status: null }], new Set([sid])));
  rerender(<ConversationSessionStatus botId="bot-a:owner" sessionId={sid} showReplying />);
  expect(screen.getByLabelText('Bot 正在回复')).toBeTruthy();
  expect(screen.queryByLabelText(/DevFlow/)).toBeNull();
});

it('status remains read-only and coexists with reply, favorite and selection controls', () => {
  const { ConversationSessionRow } = require('@/pages/Workspace/Chat/components/ConversationSessionRow');
  useSessionStatusStore.getState().patch('ctx', [{ sessionId: sid, status }], new Set([sid]));
  const session = {
    sessionId: sid,
    botId: 'bot-a:owner',
    title: 'Long session title',
    messageCount: 1,
    gmtModified: '',
    gmtCreate: '',
    favorite: false,
  };
  const onSelect = jest.fn();
  const favorite = jest.fn();
  const view = render(
    <ConversationSessionRow
      session={session}
      selected={false}
      onSelect={onSelect}
      onToggleFavorite={favorite}
      replyingSessionId={sid}
    />,
  );
  expect(screen.getByLabelText('DevFlow：进行中')).toBeTruthy();
  expect(screen.queryByLabelText('Bot 正在回复')).toBeNull();
  expect(screen.getByRole('button', { name: '收藏会话' })).toBeTruthy();
  act(() => screen.getByRole('button', { name: /Long session title/ }).click());
  expect(onSelect).toHaveBeenCalledTimes(1);
  view.rerender(<ConversationSessionRow session={session} selected={false} readOnly onSelect={onSelect} />);
  expect(screen.getByLabelText('DevFlow：进行中')).toBeTruthy();
  expect(screen.getByText('只读')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '收藏会话' })).toBeNull();
});
