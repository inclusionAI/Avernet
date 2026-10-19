import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { RepairCandidatesResponse } from '../../../../../server/contracts/repair-workbench';
const api = vi.hoisted(() => ({ candidates: vi.fn() }));
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: api }));
import RepairSelectionWorkspace from '../RepairSelectionWorkspace';

it('folds previously adopted suggestions but lets users expand, reselect and open the exact previous round', async () => {
  const done = { itemId: 'done', state: 'awaiting_verification', sourceAvailable: true, updatedAtMs: 1,
    activeTaskId: 'prior', activeRevision: 1, proposal: { summary: 'Adopted goal' }, context: {}, sources: [] };
  const pending = { ...done, itemId: 'pending', state: 'pending', activeTaskId: null, proposal: { summary: 'New goal' } };
  api.candidates.mockResolvedValue({ items: [pending, done], canEdit: true, tasks: [{ taskId: 'prior', roundNumber: 2, revision: 1, phase: 'published' }],
    facets: { nodes: [], failureModes: [] }, page: { page: 1, totalPages: 1, total: 2 } } as unknown as RepairCandidatesResponse);
  const onToggle = vi.fn(), onTask = vi.fn();
  render(<RepairSelectionWorkspace workflowId="wf" includeHistorical={false} refreshToken={0} selected={[]} selectedItems={[]}
    instructions="" busy={false} reason="" error="" onInstructions={vi.fn()} onToggle={onToggle} onResult={vi.fn()}
    onDetail={vi.fn()} onGenerate={vi.fn()} onBack={vi.fn()} onTask={onTask} />);
  expect(await screen.findByRole('checkbox', { name: '选择 New goal' })).toBeEnabled();
  expect(screen.queryByRole('checkbox', { name: '选择 Adopted goal' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /已采纳与已验证的建议/ }));
  const checkbox = screen.getByRole('checkbox', { name: '选择 Adopted goal' });
  expect(checkbox).toBeEnabled();
  await userEvent.click(checkbox);
  expect(onToggle).toHaveBeenCalledWith('done');
  const row = within(checkbox.closest('tr')!);
  await userEvent.click(row.getByRole('button', { name: /查看第 2 轮/ }));
  expect(onTask).toHaveBeenCalledWith('prior');
});
