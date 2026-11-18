/** @jest-environment jsdom */
import { notifyError } from '@/components/ui/notify';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortManagementResource } from '@/hooks/taskEscortParity/useTaskEscortManagement';
import { useErrorNotifyObserver } from '@/hooks/useErrorNotifyObserver';
import { managementService } from '@/services/taskEscortParity/managementService';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { act, cleanup, render, screen } from '@testing-library/react';
import { useCallback } from 'react';

// Keep controller, backendRequest, notification queue and observer REAL; only the delivery sink and fetch are mocked.
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn() }));
const delivered = jest.mocked(notifyError);
const sentinel = 'appSecret=DUMMY_MANAGEMENT_NOTIFY_SECRET';
const context: TaskEscortParityContext = {
  scopeKey: 'space:actor',
  ready: true,
  userId: 'actor',
  workflowId: 'wf',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const originalFetch = global.fetch;
let fetchMock: jest.Mock;
function response(ok: boolean, data: unknown): Response {
  return {
    ok,
    status: ok ? 200 : 500,
    headers: { get: () => 'application/json' },
    text: async () => JSON.stringify(data),
    json: async () => data,
  } as unknown as Response;
}
function Observer() {
  useErrorNotifyObserver();
  return null;
}
function Panel({ ctx }: { ctx: TaskEscortParityContext }) {
  const read = useCallback(
    (signal: AbortSignal) => managementService.notification(ctx.workflowId, ctx.userId!, signal),
    [ctx.workflowId, ctx.userId],
  );
  const resource = useTaskEscortManagementResource(ctx, 'notification-security', read);
  return <p data-testid="inline-error">{resource.error ?? ''}</p>;
}
function App({ ctx = context, panel = true }: { ctx?: TaskEscortParityContext; panel?: boolean }) {
  return (
    <>
      <Observer />
      {panel && <Panel ctx={ctx} />}
    </>
  );
}
async function microtasks() {
  await act(async () => {
    for (let i = 0; i < 24; i += 1) await Promise.resolve();
  });
}
function assertNoDeliveryAfterTimers() {
  expect(delivered).not.toHaveBeenCalled();
  act(() => {
    jest.runOnlyPendingTimers();
  });
  expect(useErrorNotifyStore.getState().queue).toEqual([]);
  act(() => {
    jest.runOnlyPendingTimers();
  });
  expect(delivered).not.toHaveBeenCalled();
  expect(document.body.textContent).not.toContain(sentinel);
}
beforeEach(() => {
  jest.useFakeTimers();
  useErrorNotifyStore.getState().reset();
  delivered.mockClear();
  fetchMock = jest.fn().mockResolvedValue(response(false, { message: sentinel, data: { appSecret: sentinel } }));
  global.fetch = fetchMock as typeof fetch;
});
afterEach(() => {
  cleanup();
  useErrorNotifyStore.getState().reset();
  global.fetch = originalFetch;
  jest.clearAllTimers();
  jest.useRealTimers();
});

it('preserves only cancellation keys on the safe service error across the REAL controller/httpClient chain', async () => {
  const failure = await managementService
    .notification('wf', 'actor', new AbortController().signal)
    .catch((error) => error as Error);
  const queued = useErrorNotifyStore.getState().queue;
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining('/workflows/wf/notification-config?user_id=actor'),
    expect.objectContaining({ method: 'GET' }),
  );
  expect(queued).toHaveLength(1);
  expect(queued[0].message).toContain(sentinel); // Proves the actual transport enqueued the upstream body.
  expect(queued[0].toastKey).not.toContain(sentinel);
  expect(failure).toMatchObject({ message: '500：读取失败，请重试', toastKey: queued[0].toastKey });
  for (const field of ['data', 'cause', 'response', 'apiPath', 'alreadyHandled'])
    expect(failure).not.toHaveProperty(field);
  expect(JSON.stringify(failure)).not.toContain(sentinel);
  expect(String(failure)).not.toContain(sentinel);
});

it('cancels queued RAW upstream failure during microtasks, before the REAL observer can deliver a global notification', async () => {
  render(<App />);
  await microtasks();
  const queue = useErrorNotifyStore.getState().queue;
  expect(queue).toHaveLength(1);
  expect(queue[0]).toMatchObject({ cancelled: true, message: sentinel });
  expect(screen.getByTestId('inline-error').textContent).toBe('500：读取失败，请重试');
  expect(document.body.textContent).not.toContain(sentinel);
  assertNoDeliveryAfterTimers();
});

it.each(['identity', 'disabled', 'unmounted'] as const)(
  'still cancels late aborted rejection after the resource is %s',
  async (boundary) => {
    let resolve!: (value: Response) => void;
    fetchMock
      .mockReturnValueOnce(
        new Promise<Response>((yes) => {
          resolve = yes;
        }),
      )
      .mockResolvedValue(response(true, null));
    const { rerender } = render(<App />);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const oldSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    if (boundary === 'identity') rerender(<App ctx={{ ...context, userId: 'other' }} />);
    else if (boundary === 'disabled') rerender(<App ctx={{ ...context, ready: false }} />);
    else rerender(<App panel={false} />);
    expect(oldSignal.aborted).toBe(true);
    // A late upstream error can still arrive when fetch/body processing ignored AbortSignal.
    resolve(response(false, { message: sentinel }));
    await microtasks();
    expect(useErrorNotifyStore.getState().queue).toEqual([
      expect.objectContaining({ cancelled: true, message: sentinel }),
    ]);
    expect(document.body.textContent).not.toContain('500');
    assertNoDeliveryAfterTimers();
  },
);

it('preserves and cancels both supported keys without retaining raw AbortError message/data/cause', async () => {
  const toastKey = 'req:/safe:123abc',
    notificationKey = 'req:/safe:456def';
  fetchMock.mockImplementation(async () => {
    useErrorNotifyStore.getState().enqueue({ toastKey, message: sentinel });
    useErrorNotifyStore.getState().enqueue({ toastKey: notificationKey, message: sentinel });
    throw Object.assign(new Error(sentinel), {
      name: 'AbortError',
      toastKey,
      notificationKey,
      data: { appSecret: sentinel },
      cause: sentinel,
    });
  });
  render(<App />);
  await microtasks();
  expect(useErrorNotifyStore.getState().queue).toEqual([
    expect.objectContaining({ toastKey, cancelled: true }),
    expect.objectContaining({ toastKey: notificationKey, cancelled: true }),
  ]);
  expect(screen.getByTestId('inline-error').textContent).toBe('请求已取消');
  assertNoDeliveryAfterTimers();
});
