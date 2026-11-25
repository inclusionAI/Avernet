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

it.each([false, true])('dispatches text and file-repair suggestions unchanged to AIS without requiring patch operations (batched=%s)', async full => {
  const f = await fixture(true, 2, full);
  f.items[0].proposal = null;
  f.items[0].instruction = '检查 query-core-data 的超时处理，增加硬超时兜底。';
  f.items[1].proposal = { summary: '修复 parse_alert.py 的异常处理' };
  f.items[1].instruction = '分析脚本实际实现后修改，不预设修改行号。';
  await f.service.tick();
  expect(f.calls).toHaveLength(1);
  const draft = (await new WorkflowAutomationRepository(f.db).recent('wf-1')).find(job => job.kind === 'auto_draft')!;
  const task = await f.workbench.task(draft.data.realTaskId);
  expect(task.latestAttempt.input.items.map(item => ({ proposal: item.proposal, instruction: item.instruction }))).toEqual([
    { proposal: null, instruction: '检查 query-core-data 的超时处理，增加硬超时兜底。' },
    { proposal: { summary: '修复 parse_alert.py 的异常处理' }, instruction: '分析脚本实际实现后修改，不预设修改行号。' },
  ]);
});

it.each([false, true])('still excludes textual goals carrying a different explicit workflow or baseline (batched=%s)', async full => {
  const f = await fixture(true, 2, full);
  f.items[0].proposal = { workflowId: 'another-workflow', summary: '修复脚本错误' };
  f.items[1].proposal = { baseSpecDigest: 'f'.repeat(64), summary: '修复旧版本脚本' };
  await f.service.tick();
  expect(f.calls).toEqual([]);
  expect((await new WorkflowAutomationRepository(f.db).recent('wf-1')).filter(job => job.kind === 'auto_draft')).toEqual([]);
});

it.each([false, true])('excludes structured changes without a baseline without blocking valid text and patch goals (batched=%s)', async full => {
  const f = await fixture(true, 4, full);
  f.items[0].proposal = null;
  f.items[0].instruction = '分析 parse_alert.py 并修复异常处理。';
  delete f.items[2].proposal!.baseSpecDigest;
  f.items[3].proposal = { summary: '旧文件修改', baseSpecDigest: null,
    patches: [{ path: 'scripts/parse_alert.py', before: 'old', after: 'new' }] };
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.tick();
  expect(f.calls).toHaveLength(1);
  const job = (await new WorkflowAutomationRepository(f.db).recent('wf-1')).find(row => row.kind === 'auto_draft')!;
  const task = await f.workbench.task(job.data.realTaskId);
  expect(task.latestAttempt.input.items.map(item => item.itemId)).toEqual(['item-0', 'item-1']);
  expect(task.latestAttempt.input.items[0]).toMatchObject({ proposal: null, instruction: '分析 parse_alert.py 并修复异常处理。' });
  if (full) expect(events(output)).toContainEqual(expect.objectContaining({ job: 'workflow_automatic_draft',
    event: 'filtered', reason: 'BASELINE_REASSESSMENT_REQUIRED', count: 2 }));
});

it('records a failed daily selection once instead of retrying candidate reads every minute', async () => {
  const f = await fixture(true, 1, true);
  const candidates = vi.spyOn(f.workbench, 'candidates').mockRejectedValue(new Error('candidate store unavailable'));
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.tick();
  f.advance(); await f.restart().tick();
  expect(candidates).toHaveBeenCalledTimes(1);
  expect(events(output).filter(event => event.job === 'workflow_policy' && event.phase === 'draft_planning' && event.event === 'failed')).toHaveLength(1);
});

it('finishes an empty daily selection once without rereading suggestions or repeating skip logs after new analyses arrive', async () => {
  const f = await fixture(true, 0, true);
  const candidates = vi.spyOn(f.workbench, 'candidates');
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.tick();
  const initialReads = candidates.mock.calls.length;
  expect(initialReads).toBeGreaterThan(0);
  f.analyze('later-today');
  for (let i = 0; i < 3; i++) { f.advance(); await f.restart().tick(); }
  expect(candidates.mock.calls).toHaveLength(initialReads);
  expect(events(output).filter(event => event.job === 'workflow_automatic_draft' && event.reason === 'NO_ELIGIBLE_SUGGESTIONS')).toHaveLength(1);
  f.advance(86400000);
  await f.restart().tick();
  expect(candidates.mock.calls.length).toBeGreaterThan(initialReads);
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
