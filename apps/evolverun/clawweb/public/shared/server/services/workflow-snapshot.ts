import { createHash } from 'node:crypto';
import type { IDatabase } from '../db.js';
import type { WorkflowSnapshotRef, WorkflowSnapshotBinding, WorkflowSnapshotInput } from '../contracts/workflow-snapshot.js';

type StoredRun = { flow_id: string; workflow_id: string; workflow_version: number | null; workflow_deploy_number: number | null;
  state_json?: string | null; workflow_snapshot_json?: string | null };
type Deployment = { workflow_id: string; version: number; deploy_number: number; spec_json: string };
const unresolved = (reason: 'LEGACY_MISSING' | 'SNAPSHOT_MISSING' | 'IDENTITY_MISMATCH'): WorkflowSnapshotBinding => ({ status: 'unresolved', reason });
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value); }
function parsed(value: string | null | undefined): Record<string, unknown> | null {
  if (!value) return null;
  try { const result: unknown = JSON.parse(value); return object(result) ? result : null; } catch { return null; }
}
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  return object(value) ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
}
export function workflowConfigurationDigest(value: unknown): string {
  return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
}
export function parseWorkflowSnapshotBinding(value: string | null | undefined): WorkflowSnapshotBinding | null {
  const binding = parsed(value);
  if (!binding) return null;
  if (binding.status === 'unresolved' && ['LEGACY_MISSING', 'SNAPSHOT_MISSING', 'IDENTITY_MISMATCH'].includes(String(binding.reason))) return unresolved(binding.reason as 'LEGACY_MISSING');
  const ref = binding.snapshot;
  if (binding.status !== 'resolved' || !object(ref) || typeof ref.workflowId !== 'string' || !ref.workflowId
    || typeof ref.configDigest !== 'string' || !/^[a-f0-9]{64}$/.test(ref.configDigest)) return unresolved('IDENTITY_MISMATCH');
  if (ref.kind === 'deployment' && Number.isSafeInteger(ref.workflowVersion) && Number(ref.workflowVersion) > 0
    && Number.isSafeInteger(ref.deployNumber) && Number(ref.deployNumber) > 0) return binding as WorkflowSnapshotBinding;
  if (ref.kind === 'saved' && typeof ref.commit === 'string' && /^[a-f0-9]{40,64}$/.test(ref.commit)
    && typeof ref.sourceFlowId === 'string' && ref.sourceFlowId) return binding as WorkflowSnapshotBinding;
  return unresolved('IDENTITY_MISMATCH');
}

/** Data association only. Never resolves mutable workflow_specs or runs Git/model work. */
export class WorkflowSnapshotRepository {
  constructor(private readonly db: IDatabase) {}
  private deployment(row: Deployment): WorkflowSnapshotBinding {
    const spec = parsed(row.spec_json);
    if (!spec) return unresolved('SNAPSHOT_MISSING');
    return { status: 'resolved', snapshot: { kind: 'deployment', workflowId: row.workflow_id,
      workflowVersion: Number(row.version), deployNumber: Number(row.deploy_number), configDigest: workflowConfigurationDigest(spec) } };
  }
  private saved(row: StoredRun): WorkflowSnapshotBinding {
    const state = parsed(row.state_json), pin = state?.workflowPin, spec = state?.workflowSnapshot;
    if (!object(pin) || pin.source !== 'remote-snapshot' || !object(spec)) return unresolved('LEGACY_MISSING');
    if (pin.workflowId !== row.workflow_id || spec.id !== row.workflow_id) return unresolved('IDENTITY_MISMATCH');
    const match = /^remote-snapshot:([a-f0-9]{40,64})$/.exec(String(pin.workflowDigest));
    if (!match) return unresolved('IDENTITY_MISMATCH');
    return { status: 'resolved', snapshot: { kind: 'saved', workflowId: row.workflow_id, commit: match[1], sourceFlowId: row.flow_id,
      configDigest: workflowConfigurationDigest(spec), ...(typeof pin.packDigest === 'string' ? { packDigest: pin.packDigest } : {}) } };
  }
  /** Called only on registration, never by list/detail reads. Old managed schemas remain valid. */
  private async hasAssociationColumn(): Promise<boolean> {
    try { await this.db.query('SELECT workflow_snapshot_json FROM flow_runs WHERE 1 = 0'); return true; }
    catch (error) {
      if (/no such column: workflow_snapshot_json|unknown column ['`]?workflow_snapshot_json/i.test(String(error))) return false;
      throw error;
    }
  }
  async associateRun(flowId: string): Promise<void> {
    if (!await this.hasAssociationColumn()) return;
    const binding = await this.forRun(flowId);
    await this.db.exec('UPDATE flow_runs SET workflow_snapshot_json = ? WHERE flow_id = ?', [JSON.stringify(binding), flowId]);
  }
  async forRun(flowId: string): Promise<WorkflowSnapshotBinding> {
    const row = (await this.db.query<StoredRun>('SELECT * FROM flow_runs WHERE flow_id = ?', [flowId]))[0];
    if (!row) return unresolved('LEGACY_MISSING');
    const stored = parseWorkflowSnapshotBinding(row.workflow_snapshot_json);
    if (stored?.status === 'resolved') {
      if (stored.snapshot.workflowId !== row.workflow_id) return unresolved('IDENTITY_MISMATCH');
      return stored;
    }
    if (row.workflow_version === -1) return this.saved(row);
    if (!row.workflow_deploy_number || !row.workflow_version) return stored ?? unresolved('LEGACY_MISSING');
    const history = await this.db.query<Deployment>('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND deploy_number = ?', [row.workflow_id, row.workflow_deploy_number]);
    if (!history.length) return unresolved('SNAPSHOT_MISSING');
    if (history.length !== 1 || Number(history[0].version) !== Number(row.workflow_version)) return unresolved('IDENTITY_MISMATCH');
    return this.deployment(history[0]);
  }
  /** A page of runs shares deployment reads; no per-run Git or configuration lookup. */
  async forRuns(flowIds: string[]): Promise<Map<string, WorkflowSnapshotBinding>> {
    const result = new Map<string, WorkflowSnapshotBinding>();
    const rows: StoredRun[] = [];
    const ids = [...new Set(flowIds)];
    if (!ids.length) return result;
    const association = await this.hasAssociationColumn() ? 'workflow_snapshot_json' : 'NULL AS workflow_snapshot_json';
    for (let start = 0; start < ids.length; start += 200) {
      const chunk = ids.slice(start, start + 200);
      rows.push(...await this.db.query<StoredRun>(`SELECT flow_id, workflow_id, workflow_version, workflow_deploy_number, CASE WHEN workflow_version = -1 THEN state_json ELSE NULL END AS state_json, ${association} FROM flow_runs WHERE flow_id IN (${chunk.map(() => '?').join(',')})`, chunk));
    }
    const deployments = new Map<string, Deployment[]>();
    const byWorkflow = new Map<string, Set<number>>();
    for (const row of rows) if (Number(row.workflow_version) > 0 && row.workflow_deploy_number) {
      const numbers = byWorkflow.get(row.workflow_id) ?? new Set<number>();
      numbers.add(Number(row.workflow_deploy_number)); byWorkflow.set(row.workflow_id, numbers);
    }
    for (const [workflowId, numbers] of byWorkflow) {
      const values = [...numbers];
      for (let start = 0; start < values.length; start += 200) {
        const chunk = values.slice(start, start + 200);
        const history = await this.db.query<Deployment>(`SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND deploy_number IN (${chunk.map(() => '?').join(',')})`, [workflowId, ...chunk]);
        for (const row of history) { const key = JSON.stringify([workflowId, Number(row.deploy_number)]); deployments.set(key, [...(deployments.get(key) ?? []), row]); }
      }
    }
    const bindings = new Map<string, WorkflowSnapshotBinding>();
    for (const row of rows) {
      const stored = parseWorkflowSnapshotBinding(row.workflow_snapshot_json);
      let binding: WorkflowSnapshotBinding;
      if (stored?.status === 'resolved') binding = stored.snapshot.workflowId === row.workflow_id ? stored : unresolved('IDENTITY_MISMATCH');
      else if (Number(row.workflow_version) === -1) binding = this.saved(row);
      else if (!row.workflow_deploy_number || !row.workflow_version) binding = stored ?? unresolved('LEGACY_MISSING');
      else {
        const key = JSON.stringify([row.workflow_id, Number(row.workflow_deploy_number)]);
        const history = deployments.get(key) ?? [];
        if (!history.length) binding = unresolved('SNAPSHOT_MISSING');
        else if (history.length !== 1 || Number(history[0].version) !== Number(row.workflow_version)) binding = unresolved('IDENTITY_MISMATCH');
        else { binding = bindings.get(key) ?? this.deployment(history[0]); bindings.set(key, binding); }
      }
      result.set(row.flow_id, binding);
    }
    for (const id of ids) if (!result.has(id)) result.set(id, unresolved('LEGACY_MISSING'));
    return result;
  }
  async active(workflowId: string): Promise<WorkflowSnapshotBinding | null> {
    const rows = await this.db.query<Deployment>('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [workflowId]);
    if (!rows.length) return null;
    return rows.length === 1 ? this.deployment(rows[0]) : unresolved('IDENTITY_MISMATCH');
  }
  async read(ref: WorkflowSnapshotRef): Promise<WorkflowSnapshotInput> {
    let spec: Record<string, unknown> | null = null;
    if (ref.kind === 'deployment') {
      const rows = await this.db.query<Deployment>('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND deploy_number = ?', [ref.workflowId, ref.deployNumber]);
      if (rows.length > 1 || rows.length === 1 && Number(rows[0].version) !== ref.workflowVersion) throw new Error('IDENTITY_MISMATCH');
      spec = rows.length === 1 ? parsed(rows[0].spec_json) : null;
    } else {
      const row = (await this.db.query<StoredRun>('SELECT * FROM flow_runs WHERE flow_id = ?', [ref.sourceFlowId]))[0];
      if (row) {
        const actual = this.saved(row);
        if (actual.status !== 'resolved' || actual.snapshot.kind !== 'saved' || actual.snapshot.commit !== ref.commit
          || actual.snapshot.workflowId !== ref.workflowId) throw new Error('IDENTITY_MISMATCH');
        const value = parsed(row.state_json)?.workflowSnapshot;
        spec = object(value) ? value : null;
      }
    }
    if (!spec) throw new Error('WORKFLOW_SNAPSHOT_UNAVAILABLE');
    const digest = workflowConfigurationDigest(spec);
    if (digest !== ref.configDigest) throw new Error('IDENTITY_MISMATCH');
    return { workflowSpec: spec, workflowSpecDigest: digest, workflowSnapshot: ref };
  }
}
