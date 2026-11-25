/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import {
  useTaskEscortManagement,
  useTaskEscortManagementResource,
} from '@/hooks/taskEscortParity/useTaskEscortManagement';
import { managementService } from '@/services/taskEscortParity/managementService';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/taskEscortParity/managementService', () => ({
  managementService: { access: jest.fn(), history: jest.fn() },
}));
const service = jest.mocked(managementService);
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
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (value: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { resolve, reject, promise };
}
beforeEach(() => {
  jest.clearAllMocks();
  service.history.mockResolvedValue({ history: [], active: null });
  service.access.mockResolvedValue({ canView: true, canEdit: true });
});

it('never falls back to global identity and defers history until explicit VIEW', async () => {
  const old = deferred<{ canView: boolean; canEdit: boolean }>();
  service.access.mockReturnValue(old.promise);
  const { result, rerender } = renderHook(({ ctx }) => useTaskEscortManagement(ctx), {
    initialProps: { ctx: { ...context, userId: undefined } as TaskEscortParityContext },
  });
  expect(service.access).not.toHaveBeenCalled();
  expect(result.current.canEdit).toBe(false);
  rerender({ ctx: context });
  expect(service.history).not.toHaveBeenCalled();
  await act(async () => old.resolve({ canView: false, canEdit: true }));
  expect(result.current.canEdit).toBe(false);
  expect(service.history).not.toHaveBeenCalled();
});

it.each([{ userId: 'other' }, { workflowId: 'other' }, { scopeKey: 'other' }, { ready: false }])(
  'aborts and synchronously removes permission/history on %o',
  async (patch) => {
    const pending = deferred<{ canView: boolean; canEdit: boolean }>();
    const { result, rerender } = renderHook(({ ctx }) => useTaskEscortManagement(ctx), {
      initialProps: { ctx: context },
    });
    await waitFor(() => expect(result.current.canEdit).toBe(true));
    await waitFor(() => expect(result.current.history.data).not.toBeNull());
    const signals = service.access.mock.calls.map((call) => call[2]);
    service.access.mockReturnValue(pending.promise);
    rerender({ ctx: { ...context, ...patch } });
    expect(result.current.canEdit).toBe(false);
    expect(result.current.history.data).toBeNull();
    expect(signals.every((signal) => signal.aborted)).toBe(true);
  },
);

it('cancels a privileged resource when disabled, discards late result and unmount errors', async () => {
  const old = deferred<string>(),
    next = deferred<string>();
  const read = jest.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
  const { result, rerender, unmount } = renderHook(
    ({ enabled }) => useTaskEscortManagementResource(context, 'config', read, enabled),
    { initialProps: { enabled: true } },
  );
  const oldSignal = read.mock.calls[0][0] as AbortSignal;
  rerender({ enabled: false });
  expect(oldSignal.aborted).toBe(true);
  await act(async () => old.resolve('stale privileged secret'));
  expect(result.current.data).toBeNull();
  rerender({ enabled: true });
  const nextSignal = read.mock.calls[1][0] as AbortSignal;
  unmount();
  expect(nextSignal.aborted).toBe(true);
  await act(async () => next.reject(new Error('late failure')));
});
