// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { fixture } from './workflow-automation-fixtures.js';
import { WorkflowAutomationRepository } from '../../repositories/workflow-automation-repository.js';
it('a losing environment planner skips planning but retains independent notification delivery', async () => {
  const f = await fixture(false, 0);
  const original = f.settings.list.bind(f.settings);
  let resume!: () => void;
  const gate = new Promise<void>(resolve => { resume = resolve; });
  const listed = vi.spyOn(f.settings, 'list').mockImplementationOnce(async () => { await gate; return original(); });
  const first = f.service.tick();
  try {
    await vi.waitFor(() => expect(listed).toHaveBeenCalledTimes(1));
    expect(await f.host().tick()).toBe(0);
    // One blocked planner read, one independent notification scope read;
    // no second planner scan.
    expect(listed).toHaveBeenCalledTimes(2);
  } finally { resume(); await first; }
});
it('persists planner rotation across replicas so workflows after the first batch are not starved', async () => {
  const f = await fixture(false, 0);
  const policy = await f.settings.get('wf-1');
  vi.spyOn(f.settings, 'list').mockResolvedValue(Array.from({ length: 11 }, (_, i) => ({
    ...policy, enabled: false, workflowId: `flow-${String(i + 1).padStart(2, '0')}`,
  })));
  const reads = vi.spyOn(WorkflowAutomationRepository.prototype, 'ready');
  await f.service.tick();
  reads.mockClear();
  f.advance(); await f.host().tick();
  expect(reads.mock.calls[0][0]).toBe('flow-11');
});
it('stops a planner that expired while loading settings before it can dispatch new work', async () => {
  const f = await fixture(false, 0);
  const original = f.settings.list.bind(f.settings);
  vi.spyOn(f.settings, 'list').mockImplementationOnce(async () => { f.advance(300001); return original(); });
  await expect(f.service.tick()).rejects.toThrow('AUTOMATION_PLANNER_LEASE_LOST');
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
  expect(f.calls).toHaveLength(0);
  expect((await f.service.status('wf-1')).jobs).toEqual([]);
});
it('does not let leased jobs occupy the ready-page limit and starve available work', async () => {
  const f = await fixture(false, 0);
  const repo = new WorkflowAutomationRepository(f.db);
  const now = Date.parse('2026-11-01T02:00:00Z');
  for (let i = 0; i < 4; i++) await repo.ensure('wf-1', 'auto_run', `lease-${i}`, { flowId: `lease-${i}` }, now);
  const jobs = await repo.ready('wf-1', 'auto_run', now, 4);
  for (const job of jobs.slice(0, 3)) await repo.claim(job.id, now);
  expect((await repo.ready('wf-1', 'auto_run', now, 3)).map(job => job.id)).toEqual([jobs[3].id]);
});
