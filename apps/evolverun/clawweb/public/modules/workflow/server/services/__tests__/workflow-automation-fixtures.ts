import { afterEach, vi } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { AppConfigRepository } from '../../repositories/app-config-repository.js';
import { createAutoAnalysisSettings } from '../task-guard/auto-analysis-settings.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { queueRepairCompletion } from '../repair-completion-notification.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import { createWorkflowAutomation } from '../workflow-automation-service.js';
import type { WorkflowAutomationPorts, WorkflowAutomationService, AnalysisObservation } from '../../contracts/workflow-automation.js';
const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
afterEach(async () => { for (const f of fixtures.splice(0)) await f.db.close(); vi.restoreAllMocks(); });
export async function fixture(autoDraft = false, count = 3, full = false) {
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
    CREATE TABLE workflow_deploy_history (workflow_id TEXT, deploy_number INTEGER, is_active INTEGER, version INTEGER, spec_json TEXT);`);
  f.raw.exec("INSERT INTO workflow_deploy_history VALUES ('wf-1', 7, 1, 7, '{}')");
  let clock = Date.parse('2026-11-01T02:00:00Z');
  let owner: string | null = 'owner'; let permitted = true;
  const observations = new Map<string, AnalysisObservation>();
  const settings = createAutoAnalysisSettings({ repo: new AppConfigRepository(f.db), environmentDefault: '' });
  await settings.save('wf-1', { enabled: true, autoDraft, dailyTime: '09:00', expectedVersion: 0, ...(full ? { minimumImpact: 'medium' as const } : {}) }, 'owner');
  const baseline = { ...await f.executionPort.resolveBaseline('wf-1'), activeDeployNumber: 7, specDigest: digestRepairJson({}) };
  const activeSnapshot = () => ({ status: 'resolved' as const, snapshot: { kind: 'deployment' as const,
    workflowId: 'wf-1', workflowVersion: baseline.activeDeployNumber, deployNumber: baseline.activeDeployNumber,
    configDigest: baseline.proposalSpecDigest ?? baseline.specDigest } });
  f.sourcePort.activeSnapshot = async () => activeSnapshot();
  f.executionPort.resolveBaseline = async () => baseline;
  f.executionPort.diff = async input => ({ ...input, files: [{ path: 'workflows/wf-1.yaml', status: 'M', before: 'old', after: 'new', patch: '@@ -1 +1 @@\n-old\n+new', binary: false, truncated: false }], truncated: false });
  const analyze = (flowId: string, diagnoses: unknown[] = []) => {
    f.raw.prepare(`INSERT INTO workflow_evolution_analysis_runs (analysis_id, workflow_id, flow_id, scope_type, scope_json,
      status, result_json, requested_at_ms, completed_at_ms) VALUES (?, 'wf-1', ?, 'single_run', ?, 'completed', ?, ?, ?)`)
      .run(flowId, flowId, JSON.stringify({ flowIds: [flowId], workflowSnapshot: activeSnapshot() }), JSON.stringify({ schemaVersion: 'workflow-evolution-analysis/v1',
        analysisId: flowId, facts: [], inferences: [], unknowns: [], diagnoses }), clock - 1000, clock - 1000);
    observations.set(flowId, { status: 'completed', analysisId: flowId, complete: true });
  };
  for (let i = 0; i < count; i++) {
    const proposal = { schemaVersion: 'workflow-patch/v1', workflowId: 'wf-1', baseSpecDigest: baseline.specDigest,
      summary: `修复节点 ${i}`, operations: [{ op: 'replace', nodeId: `n-${i}`, path: '/executor/timeoutSeconds', value: 60 }] };
    f.items[i].proposal = proposal;
    f.items[i].context = { sourceSnapshot: activeSnapshot(), signature: `timeout-${i}`, failureMode: 'timeout', nodeId: `n-${i}`, diagnoses: [{ flowIds: [`known-${i}`] }] };
    if (full) {
      f.items[i].context!.diagnoses = [{ flowId: `known-${i}`, severity: 'high', completedAtMs: clock - 1000 }];
      f.raw.prepare('INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)').run(100 + i, `known-${i}`, 'wf-1', 'failed', (clock - 86400000) / 1000, (clock - 86300000) / 1000, (clock - 86400000) / 1000, (clock - 86300000) / 1000);
    }
    analyze(`known-${i}`, [{ diagnosisId: `diag-${i}`, flowIds: [`known-${i}`], nodeId: `n-${i}`, failureSignature: `timeout-${i}`,
      failureMode: 'timeout', severity: 'high', reasoning: 'observed timeout', evidenceEventIds: [], proposal }]);
  }
  f.raw.prepare("INSERT INTO flow_runs VALUES (1, 'late-run', 'wf-1', 'failed', ?, ?, ?, ?)")
    .run(Math.floor((clock - 30 * 86400000) / 1000), Math.floor((clock - 30 * 86400000) / 1000), Math.floor((clock - 5000) / 1000), Math.floor((clock - 5000) / 1000));
  f.raw.exec('ALTER TABLE flow_runs ADD COLUMN workflow_version INTEGER DEFAULT 7; ALTER TABLE flow_runs ADD COLUMN workflow_deploy_number INTEGER DEFAULT 7; ALTER TABLE flow_runs ADD COLUMN state_json TEXT');
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
  } }, undefined, queueRepairCompletion);
  const create = (environment = 'prod', hostPorts = ports) => createWorkflowAutomation({ db: f.db, settings, ports: hostPorts, workbench, environment, now: () => clock });
  service = create();
  return { ...f, settings, ports, baseline, observations, analyze, workbench, get service() { return service; },
    host: create, restart(environment = 'prod') { service = create(environment); return service; }, advance(ms = 60000) { clock += ms; }, setOwner(value: string | null) { owner = value; },
    setPermission(value: boolean) { permitted = value; }, disableAtDispatch() { disableBeforeDispatch = true; } };
}
