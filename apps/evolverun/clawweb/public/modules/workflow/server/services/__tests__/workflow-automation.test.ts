// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { AppConfigRepository } from '../../repositories/app-config-repository.js';
import { createAutoAnalysisSettings } from '../task-guard/auto-analysis-settings.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createRepairReleaseService } from '../repair-release-service.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import type { RepairPublicationPort } from '../../contracts/repair-release.js';
import { createWorkflowAutomation, automationDue } from '../workflow-automation-service.js';
import { WorkflowAutomationRepository } from '../../repositories/workflow-automation-repository.js';
import type { WorkflowAutomationPorts, WorkflowAutomationService, AnalysisObservation } from '../../contracts/workflow-automation.js';
const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
afterEach(async () => { for (const f of fixtures.splice(0)) await f.db.close(); });
async function fixture(autoDraft = false, count = 3, full = false) {
  const f = await repairFixture(count); fixtures.push(f);
  for (const m of repairReleaseMigrations) for (const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
  f.raw.exec(`CREATE TABLE cm_app_config (id INTEGER PRIMARY KEY, config_key TEXT UNIQUE, config_yaml TEXT, version INTEGER, enabled INTEGER,
    description TEXT, updated_by TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT UNIQUE, workflow_id TEXT, status TEXT, started_at INTEGER,
      completed_at INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE node_executions (flow_id TEXT, node_id TEXT);
    CREATE TABLE workflow_evolution_analysis_runs (id INTEGER PRIMARY KEY, analysis_id TEXT, workflow_id TEXT, flow_id TEXT,
      scope_type TEXT, scope_json TEXT, status TEXT, result_json TEXT, requested_at_ms INTEGER, completed_at_ms INTEGER, state_version INTEGER DEFAULT 0);
    CREATE TABLE workflow_healing_diagnoses (diagnosis_id TEXT, workflow_id TEXT, flow_id TEXT, failure_signature TEXT,
      failure_mode TEXT, node_id TEXT, weak_node_id TEXT, error_text TEXT, gmt_modified INTEGER);
    CREATE TABLE workflow_deploy_history (workflow_id TEXT, deploy_number INTEGER, is_active INTEGER);`);
  let clock = Date.parse('2026-11-01T02:00:00Z');
  let owner: string | null = 'owner'; let permitted = true;
  const observations = new Map<string, AnalysisObservation>();
  const settings = createAutoAnalysisSettings({ repo: new AppConfigRepository(f.db), environmentDefault: '' });
  await settings.save('wf-1', { enabled: true, autoDraft, dailyTime: '09:00', expectedVersion: 0, ...(full ? { minimumImpact: 'medium' as const } : {}) }, 'owner');
  const baseline = { ...await f.executionPort.resolveBaseline('wf-1'), activeDeployNumber: 7 };
  f.executionPort.resolveBaseline = async () => baseline;
  f.executionPort.diff = async input => ({ ...input, files: [{ path: 'workflows/wf-1.yaml', status: 'M', before: 'old', after: 'new', patch: '@@ -1 +1 @@\n-old\n+new', binary: false, truncated: false }], truncated: false });
  const analyze = (flowId: string, diagnoses: unknown[] = []) => {
    f.raw.prepare(`INSERT INTO workflow_evolution_analysis_runs (analysis_id, workflow_id, flow_id, scope_type, scope_json,
      status, result_json, requested_at_ms, completed_at_ms) VALUES (?, 'wf-1', ?, 'single_run', ?, 'completed', ?, ?, ?)`)
      .run(flowId, flowId, JSON.stringify({ flowIds: [flowId] }), JSON.stringify({ schemaVersion: 'workflow-evolution-analysis/v1',
        analysisId: flowId, facts: [], inferences: [], unknowns: [], diagnoses }), clock - 1000, clock - 1000);
    observations.set(flowId, { status: 'completed', analysisId: flowId, complete: true });
  };
  for (let i = 0; i < count; i++) {
    const proposal = { schemaVersion: 'workflow-patch/v1', workflowId: 'wf-1', baseSpecDigest: baseline.specDigest,
      summary: `修复节点 ${i}`, operations: [{ op: 'replace', nodeId: `n-${i}`, path: '/executor/timeoutSeconds', value: 60 }] };
    f.items[i].proposal = proposal;
    f.items[i].context = { signature: `timeout-${i}`, failureMode: 'timeout', nodeId: `n-${i}`, diagnoses: [{ flowIds: [`known-${i}`] }] };
    if (full) {
      f.items[i].context!.diagnoses = [{ flowId: `known-${i}`, severity: 'high', completedAtMs: clock - 1000 }];
      f.raw.prepare('INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)').run(100 + i, `known-${i}`, 'wf-1', 'failed', (clock - 86400000) / 1000, (clock - 86300000) / 1000, (clock - 86400000) / 1000, (clock - 86300000) / 1000);
    }
    analyze(`known-${i}`, [{ diagnosisId: `diag-${i}`, flowIds: [`known-${i}`], nodeId: `n-${i}`, failureSignature: `timeout-${i}`,
      failureMode: 'timeout', severity: 'high', reasoning: 'observed timeout', evidenceEventIds: [], proposal }]);
  }
  f.raw.prepare("INSERT INTO flow_runs VALUES (1, 'late-run', 'wf-1', 'failed', ?, ?, ?, ?)")
    .run(Math.floor((clock - 30 * 86400000) / 1000), Math.floor((clock - 30 * 86400000) / 1000), Math.floor((clock - 5000) / 1000), Math.floor((clock - 5000) / 1000));
  const ports: WorkflowAutomationPorts = { owner: async () => owner, canEdit: async () => permitted,
    baseline: async () => baseline,
    analysis: { lookup: vi.fn(async id => observations.get(id) ?? { status: 'absent' as const }),
      start: vi.fn(async ({ flowId }) => { observations.set(flowId, { status: 'running' }); return { status: 'running' as const }; }) },
    notify: vi.fn(async () => ({ status: 'sent' as const })) };
  let service: WorkflowAutomationService;
  let disableBeforeDispatch = false;
  const workbench = createRepairWorkbenchService(f.db, f.sourcePort, { ...f.executionPort, async dispatch(request) {
    if (disableBeforeDispatch) await settings.set('wf-1', false, 'owner');
    if (request.input.automation) await service.authorizeDraft({ workflowId: 'wf-1', actorId: request.actorId,
      taskId: request.input.taskId, origin: request.input.automation, itemIds: request.input.items.map(item => item.itemId) });
    return f.executionPort.dispatch(request);
  } });
  const create = () => createWorkflowAutomation({ db: f.db, settings, ports, workbench, now: () => clock });
  service = create();
  return { ...f, settings, ports, baseline, observations, analyze, workbench, get service() { return service; },
    restart() { service = create(); return service; }, advance(ms = 60000) { clock += ms; }, setOwner(value: string | null) { owner = value; },
    setPermission(value: boolean) { permitted = value; }, disableAtDispatch() { disableBeforeDispatch = true; } };
}
it('uses Shanghai daily boundaries and merges missed days', () => {
  expect(automationDue(Date.parse('2026-11-01T00:59:00Z'), '09:00').key).toBe('2026-10-31');
  expect(automationDue(Date.parse('2026-11-01T01:00:00Z'), '09:00').key).toBe('2026-11-01');
});
it('claims one daily scan across hosts, analyzes late arrivals once and preserves frozen draft scope', async () => {
  const f = await fixture(true);
  await Promise.all([f.service.tick(), f.restart().tick()]);
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
  const f = await fixture(true); f.disableAtDispatch();
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
      f.baseline.activeDeployNumber = 8; f.baseline.packCommit = deployment.candidateCommit; f.baseline.specDigest = 'c'.repeat(64);
      await f.db.exec("INSERT INTO workflow_deploy_history VALUES ('wf-1', 8, 1)");
      return { status: 'succeeded', receipt: { requestId: deployment.requestId, candidateCommit: deployment.candidateCommit,
        packDigest: deployment.packDigest, deployNumber: 8, releaseVersion: 8, tagName: 'deploy/wf-1/#8', targetId: 'wf-target' } };
    }, reconcile: async () => ({ status: 'uncertain' }),
  };
  const release = createRepairReleaseService({ db: f.db, workbench: f.workbench, publisher });
  await release.publish('owner', task.taskId, { revision: 1, expectedStateVersion: task.latestAttempt.stateVersion,
    requestId: 'human-publication', targetId: 'wf-target', acknowledgeUncoveredChecks: true });
  const inbox = await f.workbench.candidates('wf-1');
  expect(inbox.items.find(item => item.itemId === task.latestAttempt.input.items[0].itemId)?.state).toBe('awaiting_verification');
  expect(inbox.items.find(item => item.itemId === task.latestAttempt.input.items[1].itemId)?.state).toBe('pending');
  expect(inbox.counts.pending).toBe(2);
  // Reassess the independently unselected scope against the actual new baseline.
  const unselected = f.items.find(item => !task.latestAttempt.input.items.some(old => old.itemId === item.itemId))!;
  unselected.proposal = { ...unselected.proposal!, baseSpecDigest: f.baseline.specDigest };
  f.analyze('new-baseline-evidence');
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
it('does not lose today runs when the incremental watermark advances before tomorrow scope', async () => {
  const f = await fixture(false, 1, true);
  const started = Date.parse('2026-11-01T00:00:00Z') / 1000;
  f.raw.prepare('INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)').run(999, 'today-run', 'wf-1', 'failed', started, started + 60, started, started + 60);
  await f.service.tick();
  expect(vi.mocked(f.ports.analysis.start).mock.calls.some(([value]) => value.flowId === 'today-run')).toBe(false);
  f.advance(86400000); await f.restart().tick(); f.advance(); await f.service.tick();
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

it('stops an automatic round before appending another batch after a deployment change',async()=>{
 const f=await fixture(true,101,true);
 for(let i=0;i<10;i++){await f.service.tick();f.advance();if(f.calls.length)break;}
 const job=(await f.service.status('wf-1')).jobs.find(j=>j.kind==='auto_draft'&&j.taskId)!;
 expect(job.taskId).toBeTruthy();
 const task=await f.workbench.task(job.taskId!);
 const draft={candidateCommit:'d'.repeat(40),parentCandidateCommit:f.baseline.packCommit,packDigest:'e'.repeat(40),itemResults:task.latestAttempt.input.items.map(item=>({itemId:item.itemId,status:'changed',reason:'fixture'}))};
 await f.workbench.report({workflowId:'wf-1',taskId:task.taskId,revision:1,expectedStateVersion:0,status:'succeeded',draft,checks:{schemaVersion:'workflow-repair-checks/v1',candidateCommit:draft.candidateCommit,packDigest:draft.packDigest,candidateDigest:digestRepairJson(draft),static:{status:'passed'},mock:{status:'not_covered'},coverage:task.latestAttempt.input.items.map(item=>({itemId:item.itemId}))}});
 f.baseline.activeDeployNumber=8;
 f.advance();await f.restart().tick();
 const record=await new WorkflowAutomationRepository(f.db).get(job.id);
 expect(record).toMatchObject({status:'blocked',reason:'BASELINE_REASSESSMENT_REQUIRED'});
 expect(record!.data.request.automation.batchIndex).toBe(0);
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
it.each([false, true])('pauses new automatic drafts across days while analysis continues, then resumes after cancellation (full=%s)', async full => {
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
  f.raw.prepare('INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)').run(999, 'new-unanalysed-run', 'wf-1', 'failed', now - 86400, now - 86300, now - 10, now - 10);
  for (let i = 0; i < 3; i++) { f.advance(); await f.restart().tick(); }
  expect(f.calls).toHaveLength(1);
  expect(vi.mocked(f.ports.analysis.start).mock.calls.some(([value]) => value.flowId === 'new-unanalysed-run')).toBe(true);
  expect((await f.workbench.task(task.taskId)).latestAttempt.input).toEqual(task.latestAttempt.input);
  const pending = await f.workbench.candidates('wf-1');
  expect(pending.tasks).toHaveLength(1);
  await f.workbench.cancel('owner', task.taskId, task.latestAttempt.revision);
  for (let i = 0; i < 3; i++) { f.advance(); await f.restart().tick(); }
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
