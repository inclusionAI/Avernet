// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { fixture } from './workflow-automation-fixtures.js';
import { WorkflowAutomationRepository } from '../../repositories/workflow-automation-repository.js';
import { IssueAggregationRepository } from '@avernet/clawevolve/server/repositories/issue-aggregation-repository';

function events(output: ReturnType<typeof vi.spyOn>) {
  return output.mock.calls.flatMap(([line]) => typeof line === 'string' && line.startsWith('[workflow-background] ')
    ? [JSON.parse(line.slice('[workflow-background] '.length))] : []);
}

it('creates a draft from existing suggestions on the due pass without scanning or enqueueing historical runs', async () => {
  const f = await fixture(true, 1, true);
  const query = f.db.query.bind(f.db);
  vi.spyOn(f.db, 'query').mockImplementation(async (sql, params) => {
    if (sql.includes('SELECT id, flow_id, COALESCE')) throw new Error('historical scan must not run');
    return query(sql, params);
  });
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.tick();
  expect(f.calls).toHaveLength(1);
  const jobs = await new WorkflowAutomationRepository(f.db).recent('wf-1');
  expect(jobs.filter(job => job.kind === 'auto_run')).toEqual([]);
  const draft = jobs.find(job => job.kind === 'auto_draft')!;
  expect(draft).toMatchObject({ status: 'waiting', attempts: 1 });
  expect(draft.data.realTaskId).toBeTruthy();
  expect(events(output)).toContainEqual(expect.objectContaining({ job: 'workflow_automatic_draft', event: 'created',
    workflowId: 'wf-1', taskId: draft.data.realTaskId, jobId: draft.id }));
});

it('ignores an unfinished historical scan instead of requiring its completion marker', async () => {
  const f = await fixture(true, 1, true);
  const repo = new WorkflowAutomationRepository(f.db);
  await repo.ensure('wf-1', 'auto_cycle', 'state', { openDue: '2026-10-23', cursor: null, completedDue: null, scannedThroughMs: null }, 0);
  await f.service.tick();
  expect(f.calls).toHaveLength(1);
});

it('does not let analysis recovery errors block creating a draft and reports partial poll failure', async () => {
  const f = await fixture(true, 1, true);
  await new WorkflowAutomationRepository(f.db).ensure('wf-1', 'auto_run', 'old-run', { flowId: 'old-run' }, 0);
  vi.mocked(f.ports.analysis.lookup).mockRejectedValue(new Error('analysis store unavailable'));
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.tick();
  expect(f.calls).toHaveLength(1);
  expect(events(output)).toContainEqual(expect.objectContaining({ job: 'workflow_policy', event: 'failed',
    workflowId: 'wf-1', phase: 'analysis_recovery', reason: 'analysis store unavailable' }));
  expect(events(output)).toContainEqual(expect.objectContaining({ job: 'workflow_automation', event: 'end', outcome: 'failed' }));
});

it('reports that no eligible suggestions exist without creating an empty draft job', async () => {
  const f = await fixture(true, 0, true);
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.tick();
  expect(f.calls).toEqual([]);
  expect((await new WorkflowAutomationRepository(f.db).recent('wf-1')).filter(job => job.kind === 'auto_draft')).toEqual([]);
  expect(events(output)).toContainEqual(expect.objectContaining({ job: 'workflow_automatic_draft', event: 'skipped',
    workflowId: 'wf-1', reason: 'NO_ELIGIBLE_SUGGESTIONS' }));
});

it('does not let notification bookkeeping failure prevent draft dispatch', async () => {
  const f = await fixture(true, 1, true);
  vi.spyOn(IssueAggregationRepository.prototype, 'listPage').mockRejectedValue(new Error('notification source unavailable'));
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.tick();
  expect(f.calls).toHaveLength(1);
  expect(events(output)).toContainEqual(expect.objectContaining({ job: 'workflow_policy', event: 'failed',
    workflowId: 'wf-1', phase: 'notification_queue', reason: 'notification source unavailable' }));
});

it('logs candidate selection failures with the workflow, phase, real code and redacted cause', async () => {
  const f = await fixture(true, 1, true);
  vi.spyOn(f.workbench, 'candidates').mockRejectedValue(Object.assign(new Error('Expected finite JSON data; password=example'), { code: 'INVALID_INPUT' }));
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.tick();
  const failure = events(output).find(event => event.job === 'workflow_policy' && event.event === 'failed');
  expect(failure).toMatchObject({ workflowId: 'wf-1', phase: 'draft_planning', errorCode: 'INVALID_INPUT',
    reason: expect.stringContaining('Expected finite JSON data') });
  expect(JSON.stringify(events(output))).not.toContain('password=example');
});

it('logs a rejected create failure without blindly retrying provider dispatch', async () => {
  const f = await fixture(true, 1, true);
  vi.spyOn(f.workbench, 'create').mockRejectedValue(Object.assign(new Error('provider rejected request'), { code: 'DISPATCH_FAILED' }));
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.tick();
  expect(events(output)).toContainEqual(expect.objectContaining({ job: 'workflow_automatic_draft', event: 'failed',
    workflowId: 'wf-1', phase: 'create_task', errorCode: 'DISPATCH_FAILED', reason: 'provider rejected request' }));
  const job = (await new WorkflowAutomationRepository(f.db).recent('wf-1')).find(row => row.kind === 'auto_draft')!;
  expect(job).toMatchObject({ status: 'blocked', reason: 'DISPATCH_FAILED' });
});

it('keeps failed-run event analysis independent of the daily draft schedule', async () => {
  const f = await fixture(false, 0, true);
  await f.service.onRunTerminal({ flowId: 'late-run', status: 'failed' });
  expect(f.ports.analysis.start).toHaveBeenCalledTimes(1);
  expect(f.calls).toEqual([]);
});

it('logs NOT_DUE before the saved time and creates one task after it without duplicate replica dispatch', async () => {
  const f = await fixture(true, 1, true);
  const clock = Date.parse('2026-11-01T02:00:00Z');
  vi.spyOn(Date, 'now').mockReturnValue(clock);
  await f.settings.save('wf-1', { enabled: true, autoDraft: true, dailyTime: '10:02', minimumImpact: 'medium', expectedVersion: 1 }, 'owner');
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.tick();
  expect(f.calls).toEqual([]);
  expect(events(output)).toContainEqual(expect.objectContaining({ job: 'workflow_automatic_draft', event: 'skipped', reason: 'NOT_DUE' }));
  f.advance(120_000);
  await Promise.all([f.service.tick(), f.host().tick()]);
  expect(f.calls).toHaveLength(1);
});
