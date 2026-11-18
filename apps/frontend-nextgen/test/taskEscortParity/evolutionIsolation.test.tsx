/** @jest-environment jsdom */
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { RepairSuggestions } from '@/pages/TaskEscort/parity/evolution/RepairSuggestions';
import { repairService } from '@/services/taskEscortParity/repairService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { useCallback } from 'react';
jest.mock('@/services/taskEscortParity/repairService', () => ({
  repairService: { candidates: jest.fn(), item: jest.fn() },
}));
const context = {
  scopeKey: 'a:wf',
  userId: 'a',
  workflowId: 'wf',
  ready: true,
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const item = {
  itemId: 'i',
  groupKey: 'g',
  proposalKey: 'p',
  contentRevision: 1,
  previousItemId: null,
  proposal: { summary: 'actual' },
  instruction: 'actual',
  sources: [],
  sourceAvailable: true,
  stateVersion: 1,
};
const candidates = {
  workflowId: 'wf',
  inputDigest: 'd1',
  items: [item],
  tasks: [],
  canEdit: true,
  capabilities: { generation: true, diff: true, publication: true, reason: null },
  page: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
};
it('aborts and render-time clears old principal/workflow data even if transport ignores abort', async () => {
  let resolveOld!: (value: string) => void;
  const signals: AbortSignal[] = [];
  const read = jest.fn((user: string, signal: AbortSignal) => {
    signals.push(signal);
    return user === 'a'
      ? new Promise<string>((resolve) => {
          resolveOld = resolve;
        })
      : Promise.resolve('new principal evidence');
  });
  const { result, rerender, unmount } = renderHook(
    ({ ctx }) => {
      const stable = useCallback((s: AbortSignal) => read(ctx.userId, s), [ctx.userId]);
      return useTaskEscortEvolutionRead(ctx, 'detail', stable);
    },
    { initialProps: { ctx: context } },
  );
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  rerender({ ctx: { ...context, scopeKey: 'b:other', userId: 'b', workflowId: 'other' } });
  expect(signals[0].aborted).toBe(true);
  expect(result.current.data).toBeNull();
  await waitFor(() => expect(result.current.data).toBe('new principal evidence'));
  await act(async () => resolveOld('private stale data'));
  expect(result.current.data).toBe('new principal evidence');
  unmount();
  expect(signals.at(-1)!.aborted).toBe(true);
});
it('clears readonly selection and cancels original detail on source digest/content/state changes', async () => {
  (repairService.candidates as jest.Mock).mockResolvedValue(candidates);
  const signals: AbortSignal[] = [];
  (repairService.item as jest.Mock).mockImplementation((_w, _i, _u, s) => {
    signals.push(s);
    return new Promise(() => {});
  });
  const { rerender } = render(<RepairSuggestions context={context} signature="sig" includeHistorical={false} />);
  const checkbox = await screen.findByRole('checkbox', { name: '预览选择建议 i' });
  fireEvent.click(checkbox);
  expect(checkbox).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: '查看修改与依据' }));
  await waitFor(() => expect(signals).toHaveLength(1));
  (repairService.candidates as jest.Mock).mockResolvedValue({
    ...candidates,
    inputDigest: 'd2',
    items: [{ ...item, contentRevision: 2, stateVersion: 2 }],
  });
  rerender(<RepairSuggestions context={context} signature="sig" includeHistorical />);
  expect(await screen.findByRole('checkbox', { name: '预览选择建议 i' })).not.toBeChecked();
  expect(signals[0].aborted).toBe(true);
});
