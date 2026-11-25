/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEditorScripts } from '@/hooks/taskEscortParity/useTaskEscortEditor';
import { backendRequest } from '@/services/backendApi/httpClient';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const context: TaskEscortParityContext = {
  scopeKey: 'one',
  ready: true,
  workflowId: 'wf',
  userId: 'alice',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const pack = {
  workflowId: 'wf',
  packId: 'pack',
  baseRef: 'origin/wf/wf',
  baseCommit: 'a'.repeat(40),
  files: [{ path: 'scripts/a.sh', content: 'private-alice', mode: '100755' }],
};
beforeEach(() => request.mockReset());
it('isolates principal, scope, workflow, read generation and signal; unrelated query does not refetch', async () => {
  const pending: Array<{ resolve: (value: unknown) => void; signal: AbortSignal }> = [];
  request.mockImplementation(
    (_url, options) =>
      new Promise((resolve) => {
        pending.push({ resolve, signal: options.signal });
      }),
  );
  const { result, rerender, unmount } = renderHook(({ value, enabled }) => useTaskEscortEditorScripts(value, enabled), {
    initialProps: { value: context, enabled: true },
  });
  await waitFor(() => expect(pending).toHaveLength(1));
  rerender({ value: { ...context, query: { nodeId: 'changed' } }, enabled: true });
  expect(request).toHaveBeenCalledTimes(1);
  rerender({ value: { ...context, scopeKey: 'two', userId: 'bob', workflowId: 'other' }, enabled: true });
  expect(result.current.data).toBeNull();
  expect(pending[0].signal.aborted).toBe(true);
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => pending[0].resolve(pack));
  expect(result.current.data).toBeNull();
  await act(async () => pending[1].resolve({ ...pack, workflowId: 'other', files: [] }));
  expect(result.current.data?.workflowId).toBe('other');
  expect(request.mock.calls[1][1].params).toEqual({ user_id: 'bob' });
  rerender({ value: { ...context, scopeKey: 'two', userId: 'bob', workflowId: 'other' }, enabled: false });
  expect(result.current.data).toBeNull();
  expect(pending[1].signal.aborted).toBe(true);
  unmount();
});
it('does not read until ready and uses explicit undefined user_id rather than ambient identity', async () => {
  request.mockResolvedValue(pack);
  const { result, rerender } = renderHook(({ value }) => useTaskEscortEditorScripts(value, true), {
    initialProps: { value: { ...context, ready: false, userId: undefined } },
  });
  expect(request).not.toHaveBeenCalled();
  rerender({ value: { ...context, ready: true, userId: undefined } });
  await waitFor(() => expect(result.current.data?.workflowId).toBe('wf'));
  expect(request.mock.calls[0][1]).toEqual(
    expect.objectContaining({ params: { user_id: undefined }, injectUserId: false, signal: expect.any(AbortSignal) }),
  );
});
