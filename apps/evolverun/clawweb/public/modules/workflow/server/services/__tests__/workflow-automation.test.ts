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
  if (!priorChanged) {
    const inbox = await f.workbench.candidates('wf-1');
    const item = await f.workbench.item('wf-1', 'item-0');
    expect(item).toMatchObject({ state: 'pending', activeTaskId: null, activeRevision: null });
    await f.workbench.create('human', { workflowId: 'wf-1', itemIds: [item.itemId], inputDigest: inbox.inputDigest, instructions: '人工重新评估', requestId: 'after-unchanged-round' });
    expect(f.calls).toHaveLength(3);
  }
});
