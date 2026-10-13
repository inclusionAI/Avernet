/** @jest-environment jsdom */
import { useServiceBotRouting } from '@/pages/Workspace/hooks/useServiceBotRouting';
import { queryIsServiceBot } from '@/services/workspace/botClassificationService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botClassificationService');
const query = jest.mocked(queryIsServiceBot);
beforeEach(() => query.mockReset().mockResolvedValue(true));

it('defaults to dynamic routing, requires an explicit stage and clears tags when switched back', async () => {
  const { result } = renderHook(() => useServiceBotRouting(['service'], true));
  expect(result.current.ready).toBe(false);
  await waitFor(() => expect(result.current.ready).toBe(true));
  expect(result.current.applyTags([{ actor_id: 'service' }])).toEqual([{ actor_id: 'service' }]);
  act(() => result.current.selectRoutingMode('service', 'specified'));
  expect(result.current.ready).toBe(false);
  act(() => result.current.selectStage('service', 'verify'));
  expect(result.current.ready).toBe(true);
  expect(result.current.applyTags([{ actor_id: 'service' }, { actor_id: 'human_1' }])).toEqual([
    { actor_id: 'service', tags: ['verify'] },
    { actor_id: 'human_1' },
  ]);
  act(() => result.current.selectRoutingMode('service', 'dynamic'));
  expect(result.current.applyTags([{ actor_id: 'service' }])).toEqual([{ actor_id: 'service' }]);
  act(() => result.current.selectRoutingMode('service', 'specified'));
  expect(result.current.ready).toBe(false);
});

it('blocks failed classification and allows retry', async () => {
  query.mockRejectedValueOnce(new Error('offline'));
  const { result } = renderHook(() => useServiceBotRouting(['service'], true));
  await waitFor(() => expect(result.current.states.service.status).toBe('error'));
  expect(result.current.ready).toBe(false);
  await act(async () => {
    await result.current.retry('service');
  });
  expect(result.current.ready).toBe(true);
});

it('ignores stale responses when a participant is removed and reselected', async () => {
  let finishOld!: (isService: boolean) => void;
  query.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishOld = resolve;
      }),
  );
  query.mockResolvedValueOnce(false);
  const { result, rerender } = renderHook(({ ids }) => useServiceBotRouting(ids, true), {
    initialProps: { ids: ['service'] },
  });
  rerender({ ids: [] });
  rerender({ ids: ['service'] });
  await waitFor(() => expect(result.current.states.service.status).toBe('non_service'));
  await act(async () => finishOld(true));
  expect(result.current.states.service.status).toBe('non_service');
});

it('clears stage choices on close and blocks until reopened classification completes', async () => {
  const { result, rerender } = renderHook(({ open }) => useServiceBotRouting(['service'], open), {
    initialProps: { open: true },
  });
  await waitFor(() => expect(result.current.ready).toBe(true));
  act(() => {
    result.current.selectRoutingMode('service', 'specified');
    result.current.selectStage('service', 'draft');
  });
  rerender({ open: false });
  expect(result.current.states).toEqual({});
  rerender({ open: true });
  await waitFor(() => expect(result.current.ready).toBe(true));
  expect(result.current.states.service).toEqual({ status: 'service', routingMode: 'dynamic' });
});
