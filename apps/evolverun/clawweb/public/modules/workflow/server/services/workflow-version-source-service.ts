import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { WorkflowVersionSource, WorkflowVersionSourceService } from '../contracts/workflow-version-source.js';
import type { RepairApproval, RepairDeployment } from '../contracts/repair-release.js';
import { digestRepairJson } from '../contracts/repair-batch.js';
import type { RepairBatchInput } from '../contracts/repair-batch.js';
import type { AutomationJob } from '../repositories/workflow-automation-repository.js';
import type { WorkflowDeployHistoryRow } from '../repositories/workflow-deploy-history-repository.js';

const unknown = (deployNumber: number): WorkflowVersionSource => ({ kind: 'unknown', deployNumber,
  baselineDeployNumber: null, taskId: null, revision: null, automationRoundId: null,
  actorId: null, effectiveAtMs: null, restoredFromDeployNumber: null });
function parse<T>(value: string | null | undefined): T | null {
  try { return value ? JSON.parse(value) as T : null; } catch { return null; }
}
function epochMs(value: number | string | Date): number | null {
  const numeric = typeof value === 'number' ? value : /^\d+(?:\.\d+)?$/.test(String(value)) ? Number(value) : NaN;
  // The shared SQL dialect stores epoch seconds. Native drivers may return Date objects.
  const result = Number.isFinite(numeric) ? (numeric > 1e12 ? numeric : numeric * 1000) : value instanceof Date ? value.getTime() : new Date(String(value).includes('T') ? value : String(value).replace(' ', 'T') + 'Z').getTime();
  return Number.isFinite(result) && result > 0 ? result : null;
}

/** Existing structured records are the authority; notes and client display fields are ignored. */
export function createWorkflowVersionSourceService(db: IDatabase): WorkflowVersionSourceService {
  type RecordRow = { record_kind: string; body_json: string };
  type RevisionRow = { task_id: string; revision: number; input_json: string; draft_json: string | null; phase: string };
  type Snapshot = { history: WorkflowDeployHistoryRow[]; records: RecordRow[]; revisions: RevisionRow[] };
  async function snapshot(workflowId: string): Promise<Snapshot> {
    const [history, records, revisions] = await Promise.all([
      db.query<WorkflowDeployHistoryRow>('SELECT deploy_number, version, action, tag_name, pack_id, owner_id, gmt_create, from_deploy_number FROM workflow_deploy_history WHERE workflow_id = ?', [workflowId]),
      db.query<RecordRow>(`SELECT record_kind, body_json FROM workflow_repair_release_records
        WHERE workflow_id = ? AND record_kind IN ('deployment', 'approval', 'auto_draft')`, [workflowId]),
      db.query<RevisionRow>(`SELECT task_id, revision, input_json, draft_json, phase FROM workflow_repair_revisions WHERE workflow_id = ? AND phase = 'published'`, [workflowId]),
    ]);
    return { history, records, revisions };
  }
  async function resolve(workflowId: string, deployNumber: number, data: Snapshot, visited = new Set<number>()): Promise<WorkflowVersionSource> {
    const result = unknown(deployNumber);
    if (visited.has(deployNumber)) return result;
    visited.add(deployNumber);
    const history = data.history.find(row => row.deploy_number === deployNumber);
    if (!history || history.action === 'edit') return result;
    if (history.action === 'rollback') {
      return { ...result, kind: 'rollback', actorId: history.owner_id || null,
        // An older version restored by a rollback retains its own origin, separately.
        restoredFromDeployNumber: history.from_deploy_number,
        ...(history.from_deploy_number != null && !visited.has(history.from_deploy_number)
          ? { restoredSource: await resolve(workflowId, history.from_deploy_number, data, visited) } : {}) };
    }
    if (history.action !== 'deploy') return result;
    const rows = data.records;
    const approvals = rows.filter(row => row.record_kind === 'approval').map(row => parse<RepairApproval>(row.body_json));
    const jobs = rows.filter(row => row.record_kind === 'auto_draft').map(row => parse<AutomationJob>(row.body_json));
    const deployments = rows.filter(row => row.record_kind === 'deployment').map(row => parse<RepairDeployment>(row.body_json));
    const matched = deployments.filter((deployment): deployment is RepairDeployment => {
      const r = deployment?.receipt;
      if (!deployment || deployment.workflowId !== workflowId || deployment.status !== 'succeeded' || !r
        || r.deployNumber !== deployNumber || r.releaseVersion !== history.version || r.tagName !== history.tag_name
        || r.tagName !== `deploy/${workflowId}/#${deployNumber}` || r.requestId !== deployment.requestId
        || r.candidateCommit !== deployment.candidateCommit || r.packDigest !== deployment.packDigest
        || r.targetId !== deployment.target?.id || deployment.baselineDeployNumber >= deployNumber
        || deployment.baseline?.workflowId !== workflowId || deployment.baseline?.packId !== history.pack_id) return false;
      return approvals.some(approval => approval?.id === deployment.approvalId && approval.workflowId === workflowId
        && approval.taskId === deployment.taskId && approval.revision === deployment.revision && approval.status === 'approved'
        && approval.actorId === deployment.actorId && approval.candidateCommit === deployment.candidateCommit
        && approval.packDigest === deployment.packDigest && approval.target?.id === deployment.target?.id
        && approval.baselineDeployNumber === deployment.baselineDeployNumber);
    });
    // An ambiguous deployment association is not trustworthy provenance.
    if (matched.length !== 1) return result;
    const deployment = matched[0];
    const revision = data.revisions.find(row => row.task_id === deployment.taskId && row.revision === deployment.revision);
    const input = parse<RepairBatchInput>(revision?.input_json);
    const draft = parse<{ candidateCommit: string; packDigest: string }>(revision?.draft_json ?? null);
    if (!input || revision.phase !== 'published' || input.taskId !== deployment.taskId
      || draft?.candidateCommit !== deployment.candidateCommit || draft.packDigest !== deployment.packDigest
      || input.baseline?.activeDeployNumber !== deployment.baselineDeployNumber) return result;
    let automationRoundId: string | null = null;
    if (input.automation) {
      const origin = input.automation;
      const job = jobs.find(job => job?.id === origin.intentId && job.workflowId === workflowId
        && job.kind === 'auto_draft' && job.data.realTaskId === deployment.taskId
        && job.data.request?.automation?.intentId === origin.intentId
        && job.data.request?.automation?.policyVersion === origin.policyVersion
        && digestRepairJson(job.data.request.automation) === digestRepairJson(origin));
      // A legacy or incomplete automatic association must never be called manual.
      if (!job) return result;
      automationRoundId = job.id;
    }
    return { ...result, kind: automationRoundId ? 'auto_optimization' : 'manual_optimization',
      baselineDeployNumber: deployment.baselineDeployNumber, taskId: deployment.taskId, revision: deployment.revision,
      automationRoundId, actorId: deployment.actorId, effectiveAtMs: epochMs(history.gmt_create) };
  }
  return {
    async getSource(workflowId, deployNumber) { return resolve(workflowId, deployNumber, await snapshot(workflowId)); },
    async forWorkflow(workflowId) {
      const data = await snapshot(workflowId);
      return new Map(await Promise.all(data.history.filter(row => row.action !== 'edit').map(async row =>
        [row.deploy_number, await resolve(workflowId, row.deploy_number, data)] as const)));
    },
  };
}
