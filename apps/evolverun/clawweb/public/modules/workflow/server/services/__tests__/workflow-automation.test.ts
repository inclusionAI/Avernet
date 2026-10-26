// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { fixture } from './workflow-automation-fixtures.js';
import { AppConfigRepository } from '../../repositories/app-config-repository.js';
import { createAutoAnalysisSettings } from '../task-guard/auto-analysis-settings.js';
import { queueRepairCompletion } from '../repair-completion-notification.js';
import { createWorkflowNotificationDrain } from '../workflow-notification-delivery.js';
import { createRepairReleaseService } from '../repair-release-service.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import type { RepairPublicationPort } from '../../contracts/repair-release.js';
import { automationDue } from '../workflow-automation-service.js';
import { WorkflowAutomationRepository } from '../../repositories/workflow-automation-repository.js';
async function planWithoutDispatch(f: Awaited<ReturnType<typeof fixture>>) {
  // Simulate a restart between persisted planning and provider dispatch.
  const original = WorkflowAutomationRepository.prototype.ready;
  const ready = vi.spyOn(WorkflowAutomationRepository.prototype, 'ready').mockImplementation(function (...args) {
    return args[1] === 'auto_draft' ? Promise.resolve([]) : original.apply(this, args);
  });
  try { await f.service.tick(); } finally { ready.mockRestore(); }
}
it('uses Shanghai daily boundaries and merges missed days', () => {
  expect(automationDue(Date.parse('2026-11-01T00:59:00Z'), '09:00').key).toBe('2026-10-31');
  expect(automationDue(Date.parse('2026-11-01T01:00:00Z'), '09:00').key).toBe('2026-11-01');
});
it.each([false, true])('plans automatic scope without fetching Git, including an empty scope (full=%s)', async full => {
  const f = await fixture(true, 0, full);
  const baseline = vi.spyOn(f.ports, 'baseline');
  await f.service.tick(); f.advance(); await f.service.tick();
  expect(baseline).not.toHaveBeenCalled();
  expect(f.calls).toHaveLength(0);
});
it.each([false, true])('dispatches a planned draft without rereading its Git baseline (batched=%s)', async full => {
  const f = await fixture(true, 1, full);
  await planWithoutDispatch(f);
  const job = (await f.service.status('wf-1')).jobs.find(row => row.kind === 'auto_draft')!;
  expect(job).toBeTruthy();
  expect(job.taskId).toBeUndefined();
  f.ports.baseline = async () => { throw new Error('Git unavailable after planning'); };
  f.advance(); await f.service.tick();
  const record = await new WorkflowAutomationRepository(f.db).get(job.id);
  expect(record).toMatchObject({ status: 'waiting', reason: null });
  expect(f.calls).toHaveLength(1);
  const task = await f.workbench.task(record!.data.realTaskId);
  expect(task.latestAttempt.input.baseline).toEqual(f.baseline);
});
it.each([false, true])('parks a disabled planned draft without journal churn (analysis enabled=%s)', async analysisEnabled => {
  const f = await fixture(true, 1);
  await planWithoutDispatch(f);
  const job = (await f.service.status('wf-1')).jobs.find(row => row.kind === 'auto_draft')!;
  const before = await new WorkflowAutomationRepository(f.db).get(job.id);
  const policy = await f.settings.get('wf-1');
  await f.settings.save('wf-1', { enabled: analysisEnabled, autoDraft: false, dailyTime: policy.dailyTime, expectedVersion: policy.version }, 'owner');
  f.ports.baseline = async () => { throw new Error('Git unavailable while disabled'); };
  f.advance(); await f.service.tick();
  expect(await new WorkflowAutomationRepository(f.db).get(job.id)).toEqual(before);
  expect(f.calls).toHaveLength(0);
});
it('parks disabled, never-dispatched analysis without claims, source reads or journal churn, and resumes on enable', async () => {
  const f = await fixture(false, 0);
  const repo = new WorkflowAutomationRepository(f.db);
  const job = await repo.ensure('wf-1', 'auto_run', 'parked', { flowId: 'parked' }, 0);
  await f.settings.set('wf-1', false, 'owner');
  await f.service.tick(); f.advance(); await f.service.tick();
  expect(f.ports.analysis.lookup).not.toHaveBeenCalled();
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
  expect(await repo.get(job.id)).toEqual(job);
  expect((await repo.recent('wf-1')).filter(row => row.kind === 'auto_cycle')).toEqual([]);
  await f.settings.set('wf-1', true, 'owner');
  f.advance(); await f.service.tick();
  expect(f.ports.analysis.lookup).toHaveBeenCalledWith('parked', 'wf-1');
  expect(await repo.get(job.id)).toMatchObject({ status: 'waiting', attempts: 1 });
});
it('still settles previously dispatched analysis while disabled, without replaying unknown requests', async () => {
  const f = await fixture(false, 0);
  const repo = new WorkflowAutomationRepository(f.db);
  for (let i = 0; i < 4; i++) await repo.ensure('wf-1', 'auto_run', `parked-${i}`, { flowId: `parked-${i}`, analysisId: null }, -100);
  const pending = await repo.ensure('wf-1', 'auto_run', 'sent', { flowId: 'sent' }, 0);
  const claimed = (await repo.claim(pending.id, 0))!;
  await repo.patch(claimed, { status: 'waiting', attempts: 1 }, 0, true);
  await f.settings.set('wf-1', false, 'owner');
  await f.service.tick();
  expect(await repo.get(pending.id)).toMatchObject({ status: 'uncertain', attempts: 1 });
  f.observations.set('sent', { status: 'completed', analysisId: 'result' });
  f.advance(); await f.service.tick();
  expect(await repo.get(pending.id)).toMatchObject({ status: 'done', attempts: 1 });
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
  expect(vi.mocked(f.ports.analysis.lookup).mock.calls.map(([id]) => id)).toEqual(['sent', 'sent']);
});

it('logs worker entry and its first empty environment-scoped batch once, not every poll', async () => {
  const f = await fixture();
  const info = vi.spyOn(console, 'info').mockImplementation(() => {});
  const drain = createWorkflowNotificationDrain({ db: f.db, settings: f.settings, workbench: f.workbench, ports: f.ports, environment: 'pre' });
  await drain(); await drain();
  const lines = info.mock.calls.map(([line]) => String(line));
  expect(lines.filter(line => line.includes('"event":"worker_started"'))).toHaveLength(1);
  expect(lines.filter(line => line.includes('"event":"batch_ready"'))).toHaveLength(1);
  expect(lines.filter(line => line.includes('"job":"workflow_notification_drain"') && line.includes('"event":"start"'))).toHaveLength(1);
  expect(lines.filter(line => line.includes('"job":"workflow_notification_drain"') && line.includes('"event":"end"'))).toHaveLength(1);
  expect(lines).toContainEqual(expect.stringContaining('"environment":"pre"'));
  expect(lines).toContainEqual(expect.stringContaining('"eligibleCount":0'));
});

it('logs queue preparation once with correlation IDs, without notification content', async () => {
  const f = await fixture();
  const info = vi.spyOn(console, 'info').mockImplementation(() => {});
  const repo = new WorkflowAutomationRepository(f.db);
  const data = { kind: 'completion', taskId: 'task-log', revision: 2, title: 'private-title', text: 'private-body', path: '/private' };
  const job = await repo.ensure('wf-1', 'auto_notice', 'log-once', data, 0);
  await repo.ensure('wf-1', 'auto_notice', 'log-once', data, 0);
  expect(info).toHaveBeenCalledOnce();
  const line = String(info.mock.calls[0][0]);
  expect(line.startsWith('[workflow-notification] ')).toBe(true);
  expect(JSON.parse(line.slice('[workflow-notification] '.length))).toMatchObject({ event: 'enqueue_prepared', transactionPending: true, workflowId: 'wf-1', taskId: 'task-log', revision: 2, noticeId: job.id });
  expect(line).not.toMatch(/private-title|private-body|\/private/);
});

it('logs a sanitized queue-discovery failure and still rejects it to the monitor', async () => {
  const f = await fixture();
  vi.spyOn(f.settings, 'list').mockRejectedValue(new Error('private-provider-secret'));
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const drain = createWorkflowNotificationDrain({ db: f.db, settings: f.settings, workbench: f.workbench, ports: f.ports, environment: 'prod' });
  await expect(drain()).rejects.toThrow('private-provider-secret');
  expect(warn).toHaveBeenCalledWith(expect.stringContaining('"event":"drain_failed"'));
  expect(warn.mock.calls.map(([line]) => String(line)).join('')).toContain('"stage":"settings_scan"');
  expect(warn.mock.calls.map(([line]) => String(line)).join('')).not.toContain('private-provider-secret');
  expect(f.ports.notify).not.toHaveBeenCalled();
});

it.each(['policy-yaml', 'notification-yaml', 'policy-null', 'notification-null', 'environment'])('isolates an unrelated invalid %s without losing healthy notifications', async invalid => {
  const f = await fixture(false, 0);
  const config = new AppConfigRepository(f.db);
  await f.settings.save('wf-2', { enabled: false, autoDraft: false, dailyConfigured: false, dailyTime: '09:00', executionEnvironment: 'pre', expectedVersion: 0 }, 'owner');
  await f.settings.save('wf-2', { enabled: false, autoDraft: false, dailyConfigured: false, dailyTime: '09:00', expectedVersion: 1,
    notificationRecipients: ['account'], expectedNotificationVersion: 0 }, 'owner');
  const row = (await config.listAll()).find(row => JSON.parse(row.config_yaml).workflowId === 'wf-2'
    && row.config_key.startsWith(invalid.startsWith('notification-') ? 'workflow_repair_notify_' : 'task_guard_auto_analyze_'))!;
  await f.db.exec('UPDATE cm_app_config SET config_yaml = ? WHERE config_key = ?', [invalid === 'environment'
    ? JSON.stringify({ ...JSON.parse(row.config_yaml), executionEnvironment: 'unknown' }) : invalid.endsWith('-null') ? 'null' : 'broken: [', row.config_key]);
  const notices = new WorkflowAutomationRepository(f.db);
  const bad = await notices.ensure('wf-2', 'auto_notice', 'bad-settings', { kind: 'completion', title: 'must not send', text: 'private', path: '/bad' }, 0);
  const good = await notices.ensure('wf-1', 'auto_notice', 'healthy', { kind: 'completion', title: 'healthy', text: 'ok', path: '/good' }, 0);
  await expect(f.service.tick()).resolves.toBeGreaterThanOrEqual(0);
  expect((await notices.get(good.id))?.status).toBe('done');
  expect(f.ports.notify).toHaveBeenCalledTimes(1);
  expect(f.ports.notify).toHaveBeenCalledWith(expect.objectContaining({ workflowId: 'wf-1' }));
  expect(await notices.get(bad.id)).toMatchObject({ status: 'retryable', reason: 'NOTIFICATION_SETTINGS_INVALID', attempts: 0 });
  await expect(f.settings.get('wf-2')).rejects.toThrow();
  expect((await createAutoAnalysisSettings({ repo: config, environmentDefault: '*' }).list()).map(policy => policy.workflowId)).toEqual(['wf-1']);
  await expect(f.host('pre').tick()).resolves.toBeGreaterThanOrEqual(0);
  expect(f.ports.notify).toHaveBeenCalledTimes(1);
  await f.db.exec('UPDATE cm_app_config SET config_yaml = ? WHERE config_key = ?', [row.config_yaml, row.config_key]);
  f.advance(); await f.service.tick();
  expect(f.ports.notify).toHaveBeenCalledTimes(1);
  await f.host('pre').tick();
  expect(await notices.get(bad.id)).toMatchObject({ status: 'done', attempts: 0 });
  await f.host('pre').tick(); // Persisted recipient children use the next bounded batch.
  expect(f.ports.notify).toHaveBeenCalledTimes(2);
  expect(f.ports.notify).toHaveBeenLastCalledWith(expect.objectContaining({ workflowId: 'wf-2', recipientId: 'account' }));
});

it('defers a batch of invalid settings instead of starving the next healthy notice or retrying unknown sends', async () => {
  const f = await fixture(false, 0);
  const config = new AppConfigRepository(f.db), notices = new WorkflowAutomationRepository(f.db);
  await f.settings.save('wf-2', { enabled: false, autoDraft: false, dailyConfigured: false, dailyTime: '09:00', expectedVersion: 0 }, 'owner');
  const row = (await config.listByPrefix('task_guard_auto_analyze_')).find(row => JSON.parse(row.config_yaml).workflowId === 'wf-2')!;
  await f.db.exec('UPDATE cm_app_config SET config_yaml = ? WHERE config_key = ?', ['secret: [', row.config_key]);
  const logs = vi.spyOn(console, 'warn').mockImplementation(() => {});
  for (let i = 0; i < 5; i++) await notices.ensure('wf-2', 'auto_notice', `bad-${i}`, { kind: 'completion', title: 'private body', text: 'private body', path: '/bad' }, 0);
  const good = await notices.ensure('wf-1', 'auto_notice', 'healthy', { kind: 'completion', title: 'healthy', text: 'ok', path: '/good' }, 1);
  await f.service.tick(); await f.service.tick();
  expect((await notices.get(good.id))?.status).toBe('done');
  expect(f.ports.notify).toHaveBeenCalledTimes(1);
  const unknown = await notices.ensure('wf-2', 'auto_notice', 'interrupted-send', { kind: 'completion', title: 'unknown', text: 'private body', path: '/old' }, 0);
  const claimed = (await notices.claim(unknown.id, 0))!;
  await notices.patch(claimed, { status: 'dispatching', attempts: 1 }, 0, true);
  await f.service.tick();
  expect((await notices.get(unknown.id))?.status).toBe('uncertain');
  expect(f.ports.notify).toHaveBeenCalledTimes(1);
  expect(JSON.stringify(logs.mock.calls)).not.toContain('secret: [');
  expect(JSON.stringify(logs.mock.calls)).not.toContain('private body');
});

it('propagates storage outages instead of treating them as malformed configuration', async () => {
  const f = await fixture(false, 0);
  const query = vi.spyOn(f.db, 'query').mockRejectedValue(new Error('storage unavailable'));
  await expect(f.settings.list()).rejects.toThrow('storage unavailable');
  query.mockRestore();
});

it('persists the selected environment without enabling analysis or configuring a daily plan', async () => {
  const f = await fixture();
  await f.db.exec('DELETE FROM cm_app_config');
  expect((await f.settings.get('wf-1')).executionEnvironment).toBe('prod');
  const saved = await f.settings.save('wf-1', { enabled: false, autoDraft: false, dailyTime: '09:00', dailyConfigured: false,
    executionEnvironment: 'pre', expectedVersion: 0 }, 'owner');
  expect(saved).toMatchObject({ executionEnvironment: 'pre', enabled: false, dailyConfigured: false });
  await expect(f.settings.save('wf-1', { enabled: false, autoDraft: false, dailyTime: '09:00', expectedVersion: saved.version,
    executionEnvironment: 'other' as any }, 'owner')).rejects.toThrow('INVALID_AUTOMATION_POLICY');
  const toggled = await f.settings.set('wf-1', true, 'owner');
  expect(toggled).toMatchObject({ executionEnvironment: 'pre', dailyConfigured: false });
});

it('leaves a pre workflow unclaimed by prod and dispatches once across two pre hosts sharing its database', async () => {
  const f = await fixture(true);
  const policy = await f.settings.get('wf-1');
  await f.settings.save('wf-1', { enabled: policy.enabled, autoDraft: policy.autoDraft, dailyTime: policy.dailyTime, expectedVersion: policy.version, executionEnvironment: 'pre' }, 'owner');
  await f.service.tick();
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
  expect(await f.db.query("SELECT record_id FROM workflow_repair_release_records WHERE record_kind='auto_cycle'")).toEqual([]);
  const pre = f.restart('pre'), pre2 = f.host('pre');
  await Promise.all([pre.onRunTerminal({ flowId: 'late-run', status: 'failed' }), pre2.onRunTerminal({ flowId: 'late-run', status: 'failed' })]);
  await Promise.all([pre.tick(), pre2.tick()]);
  expect(f.ports.analysis.start).toHaveBeenCalledTimes(1);
  f.advance(); await Promise.all([pre.tick(), pre2.tick()]);
  expect(f.calls).toHaveLength(1);
});

it('only the chosen environment handles failed-run events', async () => {
  const f = await fixture();
  await f.host('pre').onRunTerminal({ flowId: 'late-run', status: 'failed' });
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
  await f.host('prod').onRunTerminal({ flowId: 'late-run', status: 'failed' });
  expect(f.ports.analysis.start).toHaveBeenCalledTimes(1);
});

it('does not let older prod notifications starve a pre completion, and keeps manual no-change delivery once-only', async () => {
  const logs = vi.spyOn(console, 'info').mockImplementation(() => {});
  const f = await fixture();
  const policy = await f.settings.get('wf-1');
  await f.settings.save('wf-1', { enabled: false, autoDraft: false, dailyTime: policy.dailyTime, executionEnvironment: 'pre', expectedVersion: policy.version }, 'owner');
  await f.db.exec("INSERT INTO workflow_specs VALUES ('prod-wf')");
  const repo = new WorkflowAutomationRepository(f.db);
  for (let i = 0; i < 7; i++) await repo.ensure('prod-wf', 'auto_notice', `older-${i}`,
    { kind: 'completion', title: 'prod completion', text: 'private body', path: '/task' }, 0);
  const inbox = await f.workbench.candidates('wf-1');
  const task = await f.workbench.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest, instructions: '', requestId: 'manual-no-change' });
  const draft = { candidateCommit: task.input.baseline.packCommit, parentCandidateCommit: task.input.baseline.packCommit,
    packDigest: task.input.baseline.packDigest, itemResults: [{ itemId: 'item-0', status: 'not_needed', reason: 'already satisfied' }] };
  await f.workbench.report({ workflowId: 'wf-1', taskId: task.taskId, revision: 1, expectedStateVersion: 0, status: 'no_change', draft,
    checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'item-0' }] } });
  const preNotify = vi.fn(async () => ({ status: 'sent' as const, receipt: 'provider-receipt' }));
  const pre = f.host('pre', { ...f.ports, notify: preNotify });
  await Promise.all([pre.tick(), f.host('pre', { ...f.ports, notify: preNotify }).tick()]);
  expect(preNotify).toHaveBeenCalledTimes(1);
  expect(preNotify).toHaveBeenCalledWith(expect.objectContaining({ workflowId: 'wf-1', title: '优化任务已完成，无需修改', path: expect.stringContaining(task.taskId) }));
  await pre.tick(); expect(preNotify).toHaveBeenCalledTimes(1);
  const rows = await f.db.query<{body_json: string}>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind='auto_notice'");
  expect(rows.map(r => JSON.parse(r.body_json)).filter(r => r.workflowId === 'prod-wf').every(r => r.status === 'pending' && r.attempts === 0)).toBe(true);
  expect(rows.map(r => JSON.parse(r.body_json)).find(r => r.workflowId === 'wf-1')).toMatchObject({ status: 'done', data: { receipt: 'provider-receipt' } });
  const events = logs.mock.calls.filter(([line]) => String(line).startsWith('[workflow-notification] '))
    .map(([line]) => JSON.parse(String(line).slice('[workflow-notification] '.length)));
  expect(events).toContainEqual(expect.objectContaining({ event: 'accepted', taskId: task.taskId, environment: 'pre', receipt: 'provider-receipt' }));
  expect(JSON.stringify(logs.mock.calls)).not.toContain('private body');
});

it('still delivers completion notifications when automatic planning fails', async () => {
  const f = await fixture(true, 0);
  f.ports.baseline = async () => { throw new Error('baseline unavailable'); };
  const repo = new WorkflowAutomationRepository(f.db);
  await repo.ensure('wf-1', 'auto_notice', 'completed', { kind: 'completion', title: 'completed', text: 'result', path: '/task' }, 0);
  await f.service.tick();
  expect(f.ports.notify).toHaveBeenCalledTimes(1);
});

it('notifies manual draft completion even with automatic analysis disabled, once across hosts', async () => {
  const f = await fixture();
  await f.settings.set('wf-1', false, 'owner');
  const inbox = await f.workbench.candidates('wf-1');
  const task = await f.workbench.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest, instructions: '', requestId: 'manual-notice' });
  await f.workbench.report({ workflowId: 'wf-1', taskId: task.taskId, revision: 1, expectedStateVersion: 0, status: 'failed', error: { code: 'AIS_FAILED' } });
  await f.service.tick();
  expect(f.ports.notify).toHaveBeenCalledWith(expect.objectContaining({ recipientId: 'owner', path: expect.stringContaining(task.taskId), title: expect.stringContaining('失败') }));
  const sent = vi.mocked(f.ports.notify).mock.calls.length;
  f.advance(); await f.restart().tick();
  expect(f.ports.notify).toHaveBeenCalledTimes(sent);
});

it('sends configured recipients independently and does not resend successful deliveries', async () => {
  const f = await fixture();
  await f.settings.set('wf-1', false, 'owner');
  await f.settings.save('wf-1', { enabled: false, autoDraft: false, dailyTime: '09:00', expectedVersion: 2, expectedNotificationVersion: 0, notificationRecipients: ['alice', 'bob'] }, 'owner');
  const repo = new WorkflowAutomationRepository(f.db);
  await repo.ensure('wf-1', 'auto_notice', 'manual-completed', { kind: 'completion', title: '优化草稿已可审阅', text: '请审阅', path: '/task' }, 0);
  vi.mocked(f.ports.notify).mockImplementation(async notice => ({ status: notice.recipientId === 'bob' ? 'retryable' : 'sent' }));
  await f.service.tick();
  expect(f.ports.notify).not.toHaveBeenCalled();
  await f.service.tick();
  expect(vi.mocked(f.ports.notify).mock.calls.map(([notice]) => notice.recipientId).sort()).toEqual(['alice', 'bob']);
  vi.mocked(f.ports.notify).mockResolvedValue({ status: 'sent' });
  f.advance(); await f.restart().tick();
  const deliveries = vi.mocked(f.ports.notify).mock.calls.map(([notice]) => notice.recipientId);
  expect(deliveries.slice(0, 2).sort()).toEqual(['alice', 'bob']);
  expect(deliveries.slice(2)).toEqual(['bob']);
});

it('persists recipient deliveries without recursively sending them in the parent tick', async () => {
  const f = await fixture();
  await f.settings.set('wf-1', false, 'owner');
  await f.settings.save('wf-1', { enabled: false, autoDraft: false, dailyTime: '09:00', expectedVersion: 2,
    expectedNotificationVersion: 0, notificationRecipients: Array.from({ length: 20 }, (_, i) => `recipient-${i}`) }, 'owner');
  const repo = new WorkflowAutomationRepository(f.db);
  for (const key of ['completed-a', 'completed-b']) await repo.ensure('wf-1', 'auto_notice', key,
    { kind: 'completion', title: '草稿可审阅', text: '请审阅', path: '/task' }, 0);
  await f.service.tick();
  expect(f.ports.notify).not.toHaveBeenCalled();
  const rows = await f.db.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'auto_notice'");
  const jobs = rows.map(row => JSON.parse(row.body_json));
  expect(jobs.filter(job => job.data.fixedRecipient && job.status === 'pending')).toHaveLength(40);
  expect(jobs.filter(job => !job.data.fixedRecipient && job.status === 'done')).toHaveLength(2);
  for (let i = 0; i < 8; i++) {
    const before = vi.mocked(f.ports.notify).mock.calls.length;
    await f.restart().tick();
    expect(vi.mocked(f.ports.notify).mock.calls.length - before).toBeLessThanOrEqual(5);
  }
  expect(f.ports.notify).toHaveBeenCalledTimes(40);
  expect(new Set(vi.mocked(f.ports.notify).mock.calls.map(([notice]) => notice.key)).size).toBe(40);
  const completed = await f.db.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'auto_notice'");
  expect(completed.every(row => JSON.parse(row.body_json).status === 'done')).toBe(true);
});

it('bounds a hung notification batch to five concurrent deliveries and one existing timeout window', async () => {
  const f = await fixture();
  await f.settings.set('wf-1', false, 'owner');
  const repo = new WorkflowAutomationRepository(f.db);
  for (let i = 0; i < 20; i++) await repo.ensure('wf-1', 'auto_notice', `queued-${i}`,
    { kind: 'completion', fixedRecipient: true, recipientId: `recipient-${i}`, title: '草稿可审阅', text: '请审阅', path: '/task' }, 0);
  vi.mocked(f.ports.notify).mockImplementation(() => new Promise(() => {}));
  vi.useFakeTimers();
  const tick = f.service.tick(); let finished = false; void tick.then(() => { finished = true; });
  try {
    await vi.advanceTimersByTimeAsync(1);
    expect(f.ports.notify).toHaveBeenCalledTimes(5);
    await vi.advanceTimersByTimeAsync(20000);
    expect(finished).toBe(true);
    await tick;
    const rows = await f.db.query<{ body_json: string }>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'auto_notice'");
    const states = rows.map(row => JSON.parse(row.body_json).status);
    expect(states.filter(status => status === 'uncertain')).toHaveLength(5);
    expect(states.filter(status => status === 'pending')).toHaveLength(15);
  } finally { await vi.runAllTimersAsync(); await tick; vi.useRealTimers(); }
});

it('changing recipients leaves the automatic policy version and already-planned draft dispatch intact', async () => {
  const f = await fixture(true);
  await f.service.tick();
  const prior = await f.settings.get('wf-1');
  const changed = await f.settings.save('wf-1', { enabled: true, autoDraft: true, dailyTime: prior.dailyTime, expectedVersion: prior.version,
    expectedNotificationVersion: 0, notificationRecipients: ['reviewer'] }, 'owner');
  expect(changed.version).toBe(prior.version);
  expect(changed.notificationVersion).toBe(1);
  f.advance(); await f.service.tick();
  expect(f.calls).toHaveLength(1);
});

it('notifies a successful manual draft with no automation settings and deduplicates repeated result reports', async () => {
  const f = await fixture();
  const info = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.db.exec('DELETE FROM cm_app_config');
  const inbox = await f.workbench.candidates('wf-1');
  const task = await f.workbench.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest, instructions: '', requestId: 'manual-success' });
  const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: task.input.baseline.packCommit, packDigest: 'e'.repeat(40), itemResults: [{ itemId: 'item-0', status: 'changed', reason: 'fixed' }] };
  const report = { workflowId: 'wf-1', taskId: task.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded' as const, draft,
    checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'item-0' }] } };
  await f.workbench.report(report);
  await f.workbench.report(report);
  expect(info).toHaveBeenCalledWith(expect.stringContaining('"event":"completion_observed"'));
  await Promise.all([f.service.tick(), f.restart().tick()]);
  expect(f.ports.notify).toHaveBeenCalledTimes(1);
  expect(f.ports.notify).toHaveBeenCalledWith(expect.objectContaining({ title: '优化草稿已可审阅', recipientId: 'owner', path: expect.stringContaining(task.taskId) }));
  expect((await f.workbench.task(task.taskId)).latestAttempt.phase).toBe('review');
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
});

it('keeps generation in drafting if its completion notification cannot be persisted', async () => {
  const f = await fixture();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const inbox = await f.workbench.candidates('wf-1');
  const task = await f.workbench.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest, instructions: '', requestId: 'notice-write-failure' });
  f.raw.exec("CREATE TRIGGER reject_notice BEFORE INSERT ON workflow_repair_release_records WHEN NEW.record_kind = 'auto_notice' BEGIN SELECT RAISE(ABORT, 'notice write failed'); END");
  await expect(f.workbench.report({ workflowId: 'wf-1', taskId: task.taskId, revision: 1, expectedStateVersion: 0, status: 'failed', error: { code: 'AIS_FAILED' } })).rejects.toThrow('notice write failed');
  expect(warn).toHaveBeenCalledWith(expect.stringContaining('"event":"enqueue_failed"'));
  expect((await f.workbench.task(task.taskId)).latestAttempt.phase).toBe('drafting');
  expect(f.ports.notify).not.toHaveBeenCalled();
});
it('claims one daily draft plan across hosts and keeps failed-run analysis independent with frozen draft scope', async () => {
  const f = await fixture(true);
  await Promise.all([f.service.tick(), f.restart().tick()]);
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
  await f.service.onRunTerminal({ flowId: 'late-run', status: 'failed' });
  expect(f.ports.analysis.start).toHaveBeenCalledTimes(1);
  f.advance(); await f.service.tick();
  expect(f.calls).toHaveLength(1);
  const state = await f.service.status('wf-1');
  expect(state.jobs.some(job => job.kind === 'auto_draft')).toBe(true);
  const draftId = state.jobs.find(job => job.kind === 'auto_draft')!.taskId!;
  const original = await f.workbench.task(draftId);
  expect(original.latestAttempt.input.automation).toMatchObject({ kind: 'daily', policyVersion: 1 });
  f.advance(3 * 86400000); await f.restart().tick();
  expect(f.ports.analysis.start).toHaveBeenCalledTimes(1);
  expect(f.calls).toHaveLength(1);
  expect((await f.workbench.task(draftId)).latestAttempt.input).toEqual(original.latestAttempt.input);
});
it('disabling before final dispatch prevents AIS and still reconciles an already-started analysis', async () => {
  const f = await fixture(true);
  await f.service.onRunTerminal({ flowId: 'late-run', status: 'failed' });
  f.disableAtDispatch();
  await f.service.tick(); f.advance(); await f.service.tick();
  expect(f.calls).toHaveLength(0);
  expect((await f.settings.get('wf-1')).enabled).toBe(false);
  f.analyze('late-run'); f.advance(); await f.restart().tick();
  const jobs = (await f.service.status('wf-1')).jobs;
  expect(jobs.find(job => job.kind === 'auto_run')).toMatchObject({ status: 'done' });
});
it('notification rejection retries only notification and re-resolves Owner permission', async () => {
  const f = await fixture();
  vi.mocked(f.ports.notify).mockResolvedValueOnce({ status: 'retryable' });
  await f.service.tick();
  const first = vi.mocked(f.ports.notify).mock.calls[0][0];
  f.setOwner('new-owner'); f.setPermission(false); f.advance(); await f.restart().tick();
  const calls = vi.mocked(f.ports.notify).mock.calls.length;
  f.setPermission(true); f.advance(); await f.service.tick();
  expect(vi.mocked(f.ports.notify).mock.calls.length).toBeGreaterThan(calls);
  expect(vi.mocked(f.ports.notify).mock.calls.filter(([notice]) => notice.key === first.key)).toHaveLength(2);
  expect(vi.mocked(f.ports.notify).mock.calls.at(-1)![0].recipientId).toBe('new-owner');
  expect(f.calls).toHaveLength(0);
});
it('unknown notification results and expired analysis dispatches are not blindly replayed', async () => {
  const f = await fixture();
  vi.mocked(f.ports.notify).mockResolvedValue({ status: 'uncertain' });
  await f.service.onRunTerminal({ flowId: 'late-run', status: 'failed' });
  await f.service.tick(); const sent = vi.mocked(f.ports.notify).mock.calls.length;
  f.advance(300000); await f.restart().tick();
  expect(f.ports.notify).toHaveBeenCalledTimes(sent);
  const repo = new WorkflowAutomationRepository(f.db);
  const run = (await repo.recent('wf-1')).find(job => job.kind === 'auto_run')!;
  f.observations.delete('late-run'); f.advance(300000); await f.service.tick();
  expect(f.ports.analysis.start).toHaveBeenCalledTimes(1);
  expect((await repo.get(run.id))!.status).toBe('uncertain');
});

async function readyDraft(f: Awaited<ReturnType<typeof fixture>>) {
  f.advance(); await f.service.tick();
  const draftJob = (await f.service.status('wf-1')).jobs.find(job => job.kind === 'auto_draft')!;
  const task = await f.workbench.task(draftJob.taskId!);
  const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: task.latestAttempt.input.baseline.packCommit,
    packDigest: 'e'.repeat(40), itemResults: task.latestAttempt.input.items.map((item, i) => ({ itemId: item.itemId, status: i === 1 ? 'not_needed' : 'changed', reason: 'controlled generation' })) };
  await f.workbench.report({ workflowId: 'wf-1', taskId: task.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft,
    checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: task.latestAttempt.input.items.map(item => ({ itemId: item.itemId })) } });
  return f.workbench.task(task.taskId);
}
it('notifies already-dispatched automatic drafts after the switch is turned off', async () => {
  const f = await fixture(true); await f.service.tick();
  const task = await readyDraft(f);
  await f.settings.set('wf-1', false, 'owner');
  f.advance(); await f.service.tick();
  const notices = vi.mocked(f.ports.notify).mock.calls.filter(([notice]) => notice.path.includes(task.taskId));
  expect(notices).toHaveLength(1);
  expect(notices[0][0]).toMatchObject({ recipientId: 'owner', title: '自动优化草稿已可审阅' });
});
it('notifies only a genuinely reviewable candidate and does not recreate it on notification retry', async () => {
  const f = await fixture(true); await f.service.tick();
  expect(vi.mocked(f.ports.notify).mock.calls.some(([notice]) => notice.title.includes('草稿'))).toBe(false);
  const task = await readyDraft(f);
  vi.mocked(f.ports.notify).mockResolvedValueOnce({ status: 'retryable' });
  f.advance(); await f.service.tick();
  const notices = () => vi.mocked(f.ports.notify).mock.calls.filter(([notice]) => notice.title.includes('草稿'));
  expect(notices()).toHaveLength(1);
  expect(notices()[0][0].path).toContain(`repairTaskId=${task.taskId}`);
  f.advance(); await f.restart().tick();
  expect(notices()).toHaveLength(2);
  expect(notices()[0][0].key).toBe(notices()[1][0].key);
  expect(f.calls).toHaveLength(1);
});
it('excludes only actually deployed changed items in the next daily round and retains not_needed and unselected scope', async () => {
  const f = await fixture(true, 6); await f.service.tick();
  const task = await readyDraft(f);
  const publisher: RepairPublicationPort = {
    targets: async () => [{ id: 'wf-target', scope: 'workflow', botId: '', ownerId: '', environment: 'pre', label: 'workflow' }],
    activeBaseline: async () => f.baseline,
    dispatch: async deployment => {
      f.baseline.activeDeployNumber = 8; f.baseline.packCommit = deployment.candidateCommit; f.baseline.specDigest = digestRepairJson({ release: 8 });
      await f.db.exec('UPDATE workflow_deploy_history SET is_active = 0');
      await f.db.exec("INSERT INTO workflow_deploy_history VALUES ('wf-1', 8, 1, 8, ?)", [JSON.stringify({ release: 8 })]);
      return { status: 'succeeded', receipt: { requestId: deployment.requestId, candidateCommit: deployment.candidateCommit,
        packDigest: deployment.packDigest, deployNumber: 8, releaseVersion: 8, tagName: 'deploy/wf-1/#8', targetId: 'wf-target' } };
    }, reconcile: async () => ({ status: 'uncertain' }),
  };
  const release = createRepairReleaseService({ db: f.db, workbench: f.workbench, publisher });
  await release.publish('owner', task.taskId, { revision: 1, expectedStateVersion: task.latestAttempt.stateVersion,
    requestId: 'human-publication', targetId: 'wf-target', acknowledgeUncoveredChecks: true });
  const inbox = await f.workbench.candidates('wf-1', { versionScope: 'all' });
  expect(inbox.items.find(item => item.itemId === task.latestAttempt.input.items[0].itemId)?.state).toBe('awaiting_verification');
  expect(inbox.items.find(item => item.itemId === task.latestAttempt.input.items[1].itemId)?.state).toBe('pending');
  expect(inbox.counts.pending).toBe(2);
  // Reassess the independently unselected scope against the actual new baseline.
  const unselected = f.items.find(item => !task.latestAttempt.input.items.some(old => old.itemId === item.itemId))!;
  unselected.proposal = { ...unselected.proposal!, baseSpecDigest: f.baseline.specDigest };
  unselected.context!.sourceSnapshot = { status: 'resolved', snapshot: { kind: 'deployment', workflowId: 'wf-1', workflowVersion: 8, deployNumber: 8, configDigest: f.baseline.specDigest } };
  f.analyze('new-baseline-evidence', [{ diagnosisId: 'new-baseline', flowIds: ['new-baseline-evidence'], nodeId: 'new-node',
    failureSignature: unselected.context!.signature, failureMode: 'timeout', severity: 'high', reasoning: 'new baseline evidence', evidenceEventIds: [], proposal: unselected.proposal }]);
  f.advance(86400000); await f.service.tick(); f.advance(); await f.service.tick();
  const jobs = (await f.service.status('wf-1')).jobs.filter(job => job.kind === 'auto_draft' && job.taskId !== task.taskId);
  expect(jobs).toHaveLength(1);
  const next = await f.workbench.task(jobs[0].taskId!);
  expect(next.latestAttempt.input.items.map(item => item.itemId)).toEqual([unselected.itemId]);
  expect((await f.workbench.task(task.taskId)).latestAttempt.input).toEqual(task.latestAttempt.input);
});
it('does not auto-select mutually exclusive proposals and lets manual scopes coexist', async () => {
  const f = await fixture(true);
  (f.items[1].proposal!.operations as any[])[0] = { ...(f.items[0].proposal!.operations as any[])[0], value: 120 };
  await f.service.tick(); f.advance(); await f.service.tick();
  const id = (await f.service.status('wf-1')).jobs.find(job => job.kind === 'auto_draft')!.taskId!;
  expect((await f.workbench.task(id)).latestAttempt.input.items.map(item => item.itemId)).toEqual(['item-2']);
  const inbox = await f.workbench.candidates('wf-1');
  await f.workbench.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest, instructions: '人工选定方案', requestId: 'manual-scope' });
  expect(f.calls).toHaveLength(2);
});
it('preserves historical pending items and explicit analysis failures without claiming full coverage', async () => {
  const f = await fixture();
  vi.mocked(f.ports.analysis.start).mockResolvedValue({ status: 'failed' });
  await f.service.onRunTerminal({ flowId: 'late-run', status: 'failed' });
  await f.service.tick();
  for (let i = 0; i < 3; i++) { f.advance(); await f.restart().tick(); }
  expect(f.ports.analysis.start).toHaveBeenCalledTimes(3);
  expect((await f.service.status('wf-1')).jobs.find(job => job.kind === 'auto_run')).toMatchObject({ status: 'blocked', reason: 'ANALYSIS_FAILED' });
  expect((await f.workbench.candidates('wf-1')).counts.pending).toBe(3);
});

it('processes every qualified item through bounded batches in one restart-safe round and task', async () => {
  const f = await fixture(true, 105, true);
  for (let i = 0; i < 10; i++) {
    await f.restart().tick(); f.advance();
    if ((await f.service.status('wf-1')).jobs.some(job => job.kind === 'auto_draft' && job.taskId)) break;
  }
  const firstJob = (await f.service.status('wf-1')).jobs.find(job => job.kind === 'auto_draft')!;
  const id = firstJob.taskId!;
  expect(id).toBeTruthy();
  let task = await f.workbench.task(id);
  expect(task.latestAttempt.input.items).toHaveLength(100);
  expect(task.scopeItemCount).toBe(105);
  for (let batch = 0; batch < 2; batch++) {
    task = await f.workbench.task(id);
    const draft = { candidateCommit: String(batch + 1).repeat(40), parentCandidateCommit: task.latestAttempt.input.parentCandidateCommit ?? f.baseline.packCommit,
      packDigest: 'e'.repeat(40), itemResults: task.latestAttempt.input.items.map(item => ({ itemId: item.itemId, status: 'changed', reason: 'controlled generation' })) };
    await f.workbench.report({ workflowId: 'wf-1', taskId: id, revision: task.latestAttempt.revision, expectedStateVersion: 0, status: 'succeeded', draft,
      checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft),
        static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: task.latestAttempt.input.items.map(item => ({ itemId: item.itemId })) } });
    f.advance(); await f.restart().tick();
    if (!batch) { f.advance(); await f.restart().tick(); }
  }
  task = await f.workbench.task(id);
  expect(task.revisions).toHaveLength(2);
  expect(task.latestAttempt.input.items).toHaveLength(5);
  expect(task.latestAttempt.input.parentCandidateCommit).toBe('1'.repeat(40));
  expect(new Set(task.revisions.flatMap(rev => rev.input.items.map(item => item.itemId))).size).toBe(105);
  expect((await f.service.status('wf-1')).round).toMatchObject({ total: 105, batches: 2, completedBatches: 2 });
  expect(f.calls).toHaveLength(2);
  expect((await f.workbench.candidates('wf-1')).counts.processing).toBe(105);
});
it('legacy strategy keeps old scope until an explicit minimum impact save', async () => {
  const f = await fixture(true);
  expect(await f.settings.get('wf-1')).toMatchObject({ scopeVersion: 'legacy', minimumImpact: 'medium' });
  await f.settings.set('wf-1', false, 'owner');
  expect((await f.settings.get('wf-1')).scopeVersion).toBe('legacy');
  const current = await f.settings.get('wf-1');
  await f.settings.save('wf-1', { enabled: false, autoDraft: true, dailyTime: '09:00', minimumImpact: 'high', expectedVersion: current.version }, 'owner');
  expect(await f.settings.get('wf-1')).toMatchObject({ scopeVersion: 'all-qualified/v1', minimumImpact: 'high' });
  await f.settings.set('wf-1', true, 'owner');
  expect((await f.settings.get('wf-1')).minimumImpact).toBe('high');
});
it('recovers a confirmed failed automatic batch using a durable request key after restart', async () => {
  const f = await fixture(true, 6, true);
  await f.service.tick(); f.advance(); await f.service.tick();
  const job = (await f.service.status('wf-1')).jobs.find(job => job.kind === 'auto_draft')!;
  const task = await f.workbench.task(job.taskId!);
  f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: 'controlled failure' });
  await f.workbench.report({ workflowId: 'wf-1', taskId: task.taskId, revision: 1, expectedStateVersion: 0, status: 'failed', error: { code: 'AIS_FAILED' } });
  f.advance(); await f.service.tick();
  const before = await f.service.status('wf-1');
  expect(before.retryableJobs?.filter(row => row.kind === 'auto_draft')).toEqual([expect.objectContaining({ id: job.id, taskId: task.taskId })]);
  f.setOwner(null);
  expect((await f.service.status('wf-1')).retryableJobs?.some(row => row.kind === 'auto_draft')).toBe(false);
  await expect(f.service.retryFailures('wf-1')).rejects.toThrow('OWNER_MISSING');
  f.setOwner('owner');
  await f.service.retryFailures('wf-1'); f.advance(); await f.restart().tick();
  const retried = await f.workbench.task(task.taskId);
  expect(retried.latestAttempt.revision).toBe(2);
  expect(retried.latestAttempt.input.automation).toEqual(task.latestAttempt.input.automation);
  expect(retried.latestAttempt.input.items).toEqual(task.latestAttempt.input.items);
  expect(f.calls).toHaveLength(2);
  f.advance(); await f.restart().tick();
  expect(f.calls).toHaveLength(2);
});
it('does not scan today or older runs on the daily timer but still analyzes explicit failure events', async () => {
  const f = await fixture(false, 1, true);
  const started = Date.parse('2026-11-01T00:00:00Z') / 1000;
  f.raw.prepare('INSERT INTO flow_runs (id,flow_id,workflow_id,status,started_at,completed_at,gmt_create,gmt_modified) VALUES (?, ?, ?, ?, ?, ?, ?, ?)').run(999, 'today-run', 'wf-1', 'failed', started, started + 60, started, started + 60);
  await f.service.tick();
  expect(vi.mocked(f.ports.analysis.start).mock.calls.some(([value]) => value.flowId === 'today-run')).toBe(false);
  f.advance(86400000); await f.restart().tick(); f.advance(); await f.service.tick();
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
  await f.service.onRunTerminal({ flowId: 'today-run', status: 'failed' });
  expect(vi.mocked(f.ports.analysis.start).mock.calls.some(([value]) => value.flowId === 'today-run')).toBe(true);
});
it.each([true, false])('notifies a final no_change round only when prior batches have publishable changes: %s', async priorChanged => {
  const f = await fixture(true, 101, true);
  for (let i = 0; i < 10; i++) {
    await f.restart().tick(); f.advance();
    if ((await f.service.status('wf-1')).jobs.some(job => job.kind === 'auto_draft' && job.taskId)) break;
  }
  const id = (await f.service.status('wf-1')).jobs.find(job => job.kind === 'auto_draft')!.taskId!;
  for (let index = 0; index < 2; index++) {
    const task = await f.workbench.task(id);
    const changed = index === 0 && priorChanged;
    const draft = { candidateCommit: priorChanged ? 'd'.repeat(40) : f.baseline.packCommit,
      parentCandidateCommit: task.latestAttempt.input.parentCandidateCommit ?? f.baseline.packCommit,
      packDigest: priorChanged ? 'e'.repeat(40) : f.baseline.packDigest,
      itemResults: task.latestAttempt.input.items.map(item => ({ itemId: item.itemId, status: changed ? 'changed' : 'not_needed', reason: 'controlled result' })) };
    await f.workbench.report({ workflowId: 'wf-1', taskId: id, revision: task.latestAttempt.revision, expectedStateVersion: 0,
      status: changed ? 'succeeded' : 'no_change', draft, checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit,
        packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' },
        coverage: task.latestAttempt.input.items.map(item => ({ itemId: item.itemId })) } });
    const ownership = await f.workbench.candidates('wf-1');
    expect(ownership.counts.processing).toBe(index === 0 ? 100 : priorChanged ? 101 : 0);
    if (index === 1 && !priorChanged) expect(ownership.counts.pending).toBe(101);
    f.advance(); await f.restart().tick();
    if (!index) { f.advance(); await f.restart().tick(); }
  }
  const task = await f.workbench.task(id);
  expect(await new WorkflowAutomationRepository(f.db).pendingAutomaticTask('wf-1')).toBe(priorChanged ? id : null);
  expect(task.latestAttempt.phase).toBe('no_change');
  expect(task.automaticProgress?.publishable).toBe(priorChanged);
  const notices = () => vi.mocked(f.ports.notify).mock.calls.filter(([notice]) => notice.title.includes('草稿'));
  // Issue notices may precede the round notice in the durable queue.
  for (let i = 0; i < 40 && !notices().length; i++) { f.advance(); await f.restart().tick(); }
  expect(notices()).toHaveLength(priorChanged ? 1 : 0);
  if (!priorChanged) expect(vi.mocked(f.ports.notify).mock.calls.filter(([notice]) => notice.title === '优化任务已完成，无需修改')).toHaveLength(1);
  if (priorChanged) expect(notices()[0][0].path).toContain(`repairTaskId=${id}`);
  f.advance(); await f.restart().tick();
  expect(notices()).toHaveLength(priorChanged ? 1 : 0);
  expect(f.calls).toHaveLength(2);
  if (priorChanged) {
    await f.workbench.cancel('owner', id, task.latestAttempt.revision);
    expect((await f.workbench.task(id)).latestAttempt.phase).toBe('cancelled');
    expect(await new WorkflowAutomationRepository(f.db).pendingAutomaticTask('wf-1')).toBeNull();
  }
  if (!priorChanged) {
    const inbox = await f.workbench.candidates('wf-1');
    const item = await f.workbench.item('wf-1', 'item-0');
    expect(item).toMatchObject({ state: 'pending', activeTaskId: null, activeRevision: null });
    await f.workbench.create('human', { workflowId: 'wf-1', itemIds: [item.itemId], inputDigest: inbox.inputDigest, instructions: '人工重新评估', requestId: 'after-unchanged-round' });
    expect(f.calls).toHaveLength(3);
  }
});
it('excludes trials from terminal analysis and daily jobs', async () => {
  const f = await fixture(false, 0);
  await f.db.exec("INSERT INTO workflow_repair_release_records (record_kind, record_id, workflow_id, task_id, revision, request_key, request_digest, body_json, state_version, created_at_ms, updated_at_ms) VALUES ('trial', 'late-run', 'wf-1', 'task', 1, 'trial-request', 'digest', '{}', 1, 1, 1)");
  await f.service.onRunTerminal({ flowId: 'late-run', status: 'failed' });
  await f.service.tick();
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
  expect(f.ports.notify).not.toHaveBeenCalled();
  expect((await f.service.status('wf-1')).jobs.filter(job => job.kind === 'auto_run')).toEqual([]);
});

it('advances an existing round without rereading Git and retains its frozen baseline',async()=>{
 const f=await fixture(true,101,true);
 for(let i=0;i<10;i++){await f.service.tick();f.advance();if(f.calls.length)break;}
 const job=(await f.service.status('wf-1')).jobs.find(j=>j.kind==='auto_draft'&&j.taskId)!;
 expect(job.taskId).toBeTruthy();
 const task=await f.workbench.task(job.taskId!);
 const draft={candidateCommit:'d'.repeat(40),parentCandidateCommit:f.baseline.packCommit,packDigest:'e'.repeat(40),itemResults:task.latestAttempt.input.items.map(item=>({itemId:item.itemId,status:'changed',reason:'fixture'}))};
 await f.workbench.report({workflowId:'wf-1',taskId:task.taskId,revision:1,expectedStateVersion:0,status:'succeeded',draft,checks:{schemaVersion:'workflow-repair-checks/v1',candidateCommit:draft.candidateCommit,packDigest:draft.packDigest,candidateDigest:digestRepairJson(draft),static:{status:'passed'},mock:{status:'not_covered'},coverage:task.latestAttempt.input.items.map(item=>({itemId:item.itemId}))}});
 f.baseline.activeDeployNumber=8;
 f.ports.baseline=async()=>{throw new Error('Git unavailable after generation');};
 f.advance();await f.restart().tick();
 const record=await new WorkflowAutomationRepository(f.db).get(job.id);
 expect(record).toMatchObject({status:'pending',reason:null});
 expect(record!.data.request.automation.batchIndex).toBe(1);
 expect(record!.data.baseline.activeDeployNumber).toBe(7);
 expect(f.calls).toHaveLength(1);
});

it('does not let an old reviewed draft permanently block a different deployment, but keeps a real in-flight task fenced',async()=>{
 const f=await fixture(false,1,true),repo=new WorkflowAutomationRepository(f.db);
 const page=await f.workbench.candidates('wf-1');
 const task=await f.workbench.create('owner',{workflowId:'wf-1',itemIds:['item-0'],inputDigest:page.inputDigest,instructions:'',requestId:'old-review-task'});
 const now=Date.parse('2026-11-01T02:00:00Z');
 const job=await repo.ensure('wf-1','auto_draft','old-review',{baseline:{activeDeployNumber:7},realTaskId:task.taskId},now);
 await f.db.exec("UPDATE workflow_repair_revisions SET phase='review' WHERE task_id=?",[task.taskId]);
 expect(await repo.activeDraft('wf-1','["deployment","wf-1",39,34]',34)).toBeNull();
 await f.db.exec("UPDATE workflow_repair_revisions SET phase='drafting' WHERE task_id=?",[task.taskId]);
 expect((await repo.activeDraft('wf-1','["deployment","wf-1",39,34]',34))?.id).toBe(job.id);
});

// A completed generation journal is not a completed human review.
it.each([false, true])('pauses new drafts while analysis continues and resumes at the next daily plan after cancellation (full=%s)', async full => {
  const f = await fixture(true, 6, full);
  await f.service.tick();
  const task = await readyDraft(f);
  f.advance(); await f.service.tick();
  expect((await f.service.status('wf-1')).jobs.find(job => job.taskId === task.taskId)?.status).toBe('done');
  const changed = f.items[0];
  changed.context = {...changed.context, recurrence:{flowIds:['new-evidence'], count:2}};
  f.analyze('additional-evidence');
  f.advance(86400000);
  const now = Date.parse('2026-11-02T02:02:00Z') / 1000;
  f.raw.prepare('INSERT INTO flow_runs (id,flow_id,workflow_id,status,started_at,completed_at,gmt_create,gmt_modified) VALUES (?, ?, ?, ?, ?, ?, ?, ?)').run(999, 'new-unanalysed-run', 'wf-1', 'failed', now - 86400, now - 86300, now - 10, now - 10);
  await f.service.onRunTerminal({ flowId: 'new-unanalysed-run', status: 'failed' });
  for (let i = 0; i < 3; i++) { f.advance(); await f.restart().tick(); }
  expect(f.calls).toHaveLength(1);
  expect(vi.mocked(f.ports.analysis.start).mock.calls.some(([value]) => value.flowId === 'new-unanalysed-run')).toBe(true);
  expect((await f.workbench.task(task.taskId)).latestAttempt.input).toEqual(task.latestAttempt.input);
  const pending = await f.workbench.candidates('wf-1');
  expect(pending.tasks).toHaveLength(1);
  await f.workbench.cancel('owner', task.taskId, task.latestAttempt.revision);
  for (let i = 0; i < 3; i++) { f.advance(); await f.restart().tick(); }
  expect(f.calls).toHaveLength(1);
  f.advance(86400000); await f.restart().tick();
  expect(f.calls).toHaveLength(2);
  expect((await f.workbench.candidates('wf-1')).tasks).toHaveLength(2);
});

it('does not pause automatic drafting for a manually created task', async () => {
 const f=await fixture(false,1,true);
 const inbox=await f.workbench.candidates('wf-1');
 await f.workbench.create('human',{workflowId:'wf-1',itemIds:['item-0'],inputDigest:inbox.inputDigest,instructions:'',requestId:'manual-only'});
 expect(await new WorkflowAutomationRepository(f.db).pendingAutomaticTask('wf-1')).toBeNull();
});

it('also pauses a previously queued new automatic task while another draft awaits review', async () => {
 const f=await fixture(true,6);await f.service.tick();const task=await readyDraft(f);
 f.advance();await f.service.tick();
 const repo=new WorkflowAutomationRepository(f.db), now=Date.parse('2026-11-01T02:03:00Z');
 const queued=await repo.ensure('wf-1','auto_draft','queued-before-review',{actorId:'owner',baseline:f.baseline},now);
 const inbox=await f.workbench.candidates('wf-1');
 const request={workflowId:'wf-1',itemIds:['item-5'],inputDigest:inbox.inputDigest,instructions:'',requestId:queued.id,automation:{kind:'daily',intentId:queued.id,policyVersion:1,evidenceCutoffMs:now}};
 const claimed=(await repo.claim(queued.id,now))!;
 await repo.patch(claimed,{data:{...queued.data,request}},now,true);
 f.advance(120000);await f.restart().tick();
 expect(f.calls).toHaveLength(1);
 expect(await repo.get(queued.id)).toMatchObject({reason:'DRAFT_IN_PROGRESS'});
 expect((await f.workbench.candidates('wf-1')).tasks.map(t=>t.taskId)).toEqual([task.taskId]);
});

it('projects run locators and evidence completeness without submitting any work', async () => {
  const f = await fixture();
  const repo = new WorkflowAutomationRepository(f.db);
  const now = Date.parse('2026-11-01T02:00:00Z');
  const waiting = await repo.ensure('wf-1', 'auto_run', 'waiting', { flowId: 'waiting-flow' }, now);
  const partial = await repo.ensure('wf-1', 'auto_run', 'partial', { flowId: 'partial-flow' }, now);
  await repo.patch((await repo.claim(waiting.id, now))!, { status: 'waiting' }, now, true);
  await repo.patch((await repo.claim(partial.id, now))!, { status: 'done', reason: 'PARTIAL_ANALYSIS', data: { flowId: 'partial-flow', analysisId: 'analysis-1', complete: false } }, now, true);
  const before = await repo.recent('wf-1');
  const state = await f.service.status('wf-1');
  expect(state.jobs).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: waiting.id, flowId: 'waiting-flow', status: 'waiting', createdAtMs: now }),
    expect.objectContaining({ id: partial.id, flowId: 'partial-flow', analysisId: 'analysis-1', complete: false, reason: 'PARTIAL_ANALYSIS' }),
  ]));
  expect(state.retryableJobs).toEqual([]);
  expect(await repo.recent('wf-1')).toEqual(before);
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
  expect(f.ports.notify).not.toHaveBeenCalled();
  expect(f.calls).toHaveLength(0);
});
it('lists exactly the analysis and notice jobs recovery can change, excluding active leases and unknown outcomes', async () => {
  const f = await fixture();
  const repo = new WorkflowAutomationRepository(f.db);
  const now = Date.parse('2026-11-01T02:00:00Z');
  async function job(key: string, kind: 'auto_run' | 'auto_notice', status: 'blocked' | 'uncertain' | 'waiting', reason: string, leased = false) {
    const record = await repo.ensure('wf-1', kind, key, { flowId: key }, now);
    await repo.patch((await repo.claim(record.id, now))!, { status, reason }, now, !leased);
    return record.id;
  }
  const analysis = await job('failed-analysis', 'auto_run', 'blocked', 'ANALYSIS_FAILED');
  const notice = await job('failed-notice', 'auto_notice', 'blocked', 'NOTIFICATION_REJECTED');
  const owner = await job('missing-owner', 'auto_run', 'blocked', 'OWNER_MISSING');
  const denied = await job('denied-owner', 'auto_notice', 'blocked', 'OWNER_PERMISSION_DENIED');
  await job('leased', 'auto_run', 'blocked', 'ANALYSIS_FAILED', true);
  await job('unknown', 'auto_run', 'uncertain', 'ANALYSIS_OUTCOME_UNKNOWN');
  await job('running', 'auto_run', 'waiting', '');
  await job('stale', 'auto_notice', 'blocked', 'CANDIDATE_SUPERSEDED');
  const before = await repo.recent('wf-1');
  const state = await f.service.status('wf-1');
  const ids = state.retryableJobs!.map(row => row.id).sort();
  expect(ids).toEqual([analysis, notice, owner, denied].sort());
  expect(await repo.recent('wf-1')).toEqual(before);
  await f.service.retryFailures('wf-1');
  const changed = (await repo.recent('wf-1')).filter(row => row.status !== before.find(old => old.id === row.id)!.status);
  expect(changed.map(row => row.id).sort()).toEqual(ids);
  expect((await f.service.status('wf-1')).retryableJobs).toEqual([]);
  expect(f.ports.analysis.start).not.toHaveBeenCalled();
  expect(f.ports.notify).not.toHaveBeenCalled();
});
it('requeues only the requested failure and leaves other failures and unknown outcomes untouched', async () => {
  const f = await fixture(); const repo = new WorkflowAutomationRepository(f.db);
  const now = Date.parse('2026-11-01T02:00:00Z');
  const jobs = [];
  for (const [kind, key, status, reason] of [
    ['auto_run', 'chosen', 'blocked', 'ANALYSIS_FAILED'], ['auto_notice', 'other', 'blocked', 'NOTIFICATION_REJECTED'],
    ['auto_run', 'unknown', 'uncertain', 'ANALYSIS_OUTCOME_UNKNOWN'],
  ] as const) {
    const job = await repo.ensure('wf-1', kind, key, { flowId: key }, now);
    await repo.patch((await repo.claim(job.id, now))!, { status, reason }, now, true); jobs.push(job);
  }
  await f.service.retryFailures('wf-1', jobs[0].id);
  expect((await repo.get(jobs[0].id))?.status).toBe('retryable');
  expect((await repo.get(jobs[1].id))?.status).toBe('blocked');
  expect((await repo.get(jobs[2].id))?.status).toBe('uncertain');
  await expect(f.service.retryFailures('wf-1', jobs[2].id)).rejects.toThrow('TASK_NOT_RETRYABLE');
  await expect(f.service.retryFailures('wf-2', jobs[1].id)).rejects.toThrow('TASK_NOT_RETRYABLE');
  expect(f.ports.analysis.start).not.toHaveBeenCalled(); expect(f.ports.notify).not.toHaveBeenCalled();
});
it('retries a targeted automatic draft without resetting unrelated failed notifications', async () => {
  const f = await fixture(true, 1, true);
  await f.service.tick(); f.advance(); await f.service.tick();
  const job = (await f.service.status('wf-1')).jobs.find(row => row.kind === 'auto_draft')!;
  const task = await f.workbench.task(job.taskId!);
  f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: 'controlled failure' });
  await f.workbench.report({ workflowId: 'wf-1', taskId: task.taskId, revision: 1, expectedStateVersion: 0, status: 'failed', error: { code: 'AIS_FAILED' } });
  f.advance(); await f.service.tick();
  const repo = new WorkflowAutomationRepository(f.db), now = Date.parse('2026-11-01T02:03:00Z');
  const notice = await repo.ensure('wf-1', 'auto_notice', 'unrelated-failure', { kind: 'completion' }, now);
  await repo.patch((await repo.claim(notice.id, now))!, { status: 'blocked', reason: 'NOTIFICATION_REJECTED' }, now, true);
  const dispatched = f.calls.length;
  await f.service.retryFailures('wf-1', job.id);
  expect((await repo.get(job.id))?.status).toBe('pending');
  expect((await repo.get(notice.id))?.status).toBe('blocked');
  expect(f.calls).toHaveLength(dispatched);
});
it('uses the same fifty-job recovery limit even when recent records are truncated', async () => {
  const f = await fixture();
  const repo = new WorkflowAutomationRepository(f.db);
  const now = Date.parse('2026-11-01T02:00:00Z');
  const records = [];
  for (let i = 0; i < 55; i++) {
    const record = await repo.ensure('wf-1', 'auto_run', `failure-${i}`, { flowId: `flow-${i}` }, now);
    await repo.patch((await repo.claim(record.id, now))!, { status: 'blocked', reason: 'ANALYSIS_FAILED' }, now, true);
    records.push(record);
  }
  const state = await f.service.status('wf-1');
  expect(state.retryableJobs).toHaveLength(50);
  await f.service.retryFailures('wf-1');
  const recovered = (await Promise.all(records.map(row => repo.get(row.id)))).filter(row => row!.status === 'retryable');
  expect(recovered.map(row => row!.id).sort()).toEqual(state.retryableJobs!.map(row => row.id).sort());
  expect((await f.service.status('wf-1')).retryableJobs).toHaveLength(5);
});

it('polls draft recovery with bounded reads despite many cancelled historical drafts', async () => {
  const f = await fixture(true, 1, true);
  await f.service.tick(); f.advance(); await f.service.tick();
  const job = (await f.service.status('wf-1')).jobs.find(row => row.kind === 'auto_draft')!;
  const task = await f.workbench.task(job.taskId!);
  f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: 'controlled failure' });
  await f.workbench.report({ workflowId: 'wf-1', taskId: task.taskId, revision: 1, expectedStateVersion: 0, status: 'failed', error: { code: 'AIS_FAILED' } });
  f.advance(); await f.service.tick();
  const revision = f.raw.prepare('SELECT * FROM workflow_repair_revisions WHERE task_id=? AND revision=1').get(task.taskId) as Record<string, unknown>;
  const journal = f.raw.prepare('SELECT * FROM workflow_repair_release_records WHERE record_id=?').get(job.id) as Record<string, unknown>;
  function insert(table: string, row: Record<string, unknown>) {
    const entries = Object.entries(row);
    f.raw.prepare(`INSERT INTO ${table} (${entries.map(([key]) => key).join(',')}) VALUES (${entries.map(() => '?').join(',')})`).run(...entries.map(([, value]) => value));
  }
  function seed(i: number, phase: string, policyVersion?: number, leased = false, staleRequest = false) {
    const taskId = `HIST-${i}`, id = digestRepairJson(['history', i]), requestId = `history-request-${i}`;
    const input = { ...JSON.parse(String(revision.input_json)), taskId };
    insert('workflow_repair_revisions', { ...revision, task_id: taskId, phase, request_id: requestId, request_key: id, input_json: JSON.stringify(input) });
    const body = JSON.parse(String(journal.body_json));
    const data = { ...body.data, realTaskId: taskId, request: { ...body.data.request, requestId: staleRequest ? 'superseded-request' : requestId,
      automation: { ...body.data.request.automation, ...(policyVersion === undefined ? {} : { policyVersion }) } } };
    insert('workflow_repair_release_records', { ...journal, record_id: id, request_key: id, created_at_ms: 1, updated_at_ms: 1,
      body_json: JSON.stringify({ ...body, id, ...(leased ? { leaseUntilMs: Number.MAX_SAFE_INTEGER } : {}), data }) });
    return id;
  }
  f.raw.transaction(() => {
    for (let i = 0; i < 200; i++) {
      seed(i, i < 80 ? 'cancelled' : 'failed', i >= 160 && i < 180 ? 0 : undefined, i >= 180, i >= 120 && i < 160);
      if (i >= 80 && i < 120) insert('workflow_repair_revisions', { ...revision, task_id: `HIST-${i}`, revision: 2, phase: 'cancelled',
        request_key: digestRepairJson(['newer', i]), input_json: JSON.stringify({ ...JSON.parse(String(revision.input_json)), taskId: `HIST-${i}`, revision: 2 }) });
    }
  })();
  const readTask = vi.spyOn(f.workbench, 'task'), query = vi.spyOn(f.db, 'query');
  const dispatched = f.calls.length;
  for (let i = 0; i < 3; i++) {
    query.mockClear();
    expect((await f.service.status('wf-1')).retryableJobs?.filter(row => row.kind === 'auto_draft').map(row => row.id)).toEqual([job.id]);
    expect(query.mock.calls.length).toBeLessThanOrEqual(10);
  }
  const recoveryQuery = query.mock.calls.find(([sql]) => sql.includes('SELECT a.body_json, r.revision'))!;
  const plan = f.raw.prepare(`EXPLAIN QUERY PLAN ${recoveryQuery[0]}`).all(...recoveryQuery[1]!) as { detail: string }[];
  expect(plan.some(row => row.detail.includes('idx_repair_release_task'))).toBe(true);
  const eligible = [job.id];
  f.raw.transaction(() => { for (let i = 200; i < 250; i++) eligible.push(seed(i, 'failed')); })();
  const targets = (await f.service.status('wf-1')).retryableJobs!.filter(row => row.kind === 'auto_draft');
  expect(targets).toHaveLength(50);
  await f.service.retryFailures('wf-1');
  const remaining = (await f.service.status('wf-1')).retryableJobs!.filter(row => row.kind === 'auto_draft');
  expect(remaining.map(row => row.id)).toEqual(eligible.filter(id => !targets.some(row => row.id === id)));
  expect(remaining).toHaveLength(1);
  expect(readTask).not.toHaveBeenCalled();
  expect(f.calls).toHaveLength(dispatched);
});

it('reads status and paged history on the original journal schema without executing writes', async () => {
  const f = await fixture();
  const repo = new WorkflowAutomationRepository(f.db);
  const job = await repo.ensure('wf-1', 'auto_run', 'legacy-run', { flowId: 'legacy-run' }, Date.parse('2026-11-01T01:00:00Z'));
  const write = vi.spyOn(f.db, 'exec');
  expect((await f.service.status('wf-1')).jobs).toEqual(expect.arrayContaining([expect.objectContaining({ id: job.id, status: 'pending' })]));
  expect(await f.service.records('wf-1', { limit: 8 })).toMatchObject({ jobs: [expect.objectContaining({ id: job.id, flowId: 'legacy-run', status: 'pending' })], nextCursor: null });
  expect(write).not.toHaveBeenCalled();
});
