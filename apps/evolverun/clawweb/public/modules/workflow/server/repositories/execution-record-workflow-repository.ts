import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { WorkflowSnapshotBinding, WorkflowSnapshotRef } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
import { parseWorkflowSnapshotBinding, workflowConfigurationDigest } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import type { ExecutionRecordWorkflow } from '../../shared/execution-record.js';
import type { ExecutionRecordSnapshot } from './flow-run-repository.js';
import {
  ExecutionRecordWorkflowDocumentError, executionRecordWorkflowYaml, parseExecutionRecordWorkflowJson,
  readExecutionRecordWorkflowSpec, workflowObject,
} from '../services/execution-record-workflow-yaml.js';

type WorkflowRun = Pick<ExecutionRecordSnapshot,
  'flow_id' | 'workflow_id' | 'workflow_version' | 'workflow_deploy_number' | 'state_json' | 'workflow_snapshot_json'>;
type Deployment = { workflow_id: string; version: number; deploy_number: number; spec_json: string };
type UnavailableReason = Extract<WorkflowSnapshotBinding, { status: 'unresolved' }>['reason'];
const unavailable = (reason: UnavailableReason): ExecutionRecordWorkflow => ({
  workflow_yaml: null, workflow_snapshot: { status: 'unresolved', reason },
});
const positiveInteger = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;

/** Only the optional association column may be absent on older managed schemas. */
export function isMissingExecutionRecordWorkflowAssociation(error: unknown): boolean {
  const message = error instanceof Error ? error.message : '';
  return /\b(?:no such column|unknown column)\s*:?\s*['"`]?workflow_snapshot_json['"`]?(?:\s|$)/i.test(message);
}

/** Bounded association read: uses captured run state, exact history only, no draft/Git/credential reads. */
export class ExecutionRecordWorkflowRepository {
  constructor(private readonly db: IDatabase) {}

  async read(run: WorkflowRun): Promise<ExecutionRecordWorkflow> {
    try {
      // A resolved stored pin is evidence, not permission to substitute another run/version/deployment.
      const stored = this.storedBinding(run);
      if (stored?.status === 'unresolved' && stored.reason === 'IDENTITY_MISMATCH') return unavailable(stored.reason);
      if (stored?.status === 'resolved' && !this.matchesRun(stored.snapshot, run)) return unavailable('IDENTITY_MISMATCH');
      let result: ExecutionRecordWorkflow;
      if (run.workflow_version === -1) result = this.saved(run);
      else if (run.workflow_version == null || run.workflow_deploy_number == null) result = unavailable('LEGACY_MISSING');
      else if (!positiveInteger(run.workflow_version) || !positiveInteger(run.workflow_deploy_number)) result = unavailable('IDENTITY_MISMATCH');
      else result = await this.deployment(run);
      if (stored?.status === 'resolved' && result.workflow_snapshot.status === 'resolved') {
        const actual = result.workflow_snapshot.snapshot;
        if (stored.snapshot.configDigest !== actual.configDigest
          || (stored.snapshot.kind === 'saved' && actual.kind === 'saved'
            && (stored.snapshot.commit !== actual.commit
              || stored.snapshot.packDigest !== undefined && stored.snapshot.packDigest !== actual.packDigest))) return unavailable('IDENTITY_MISMATCH');
      }
      return result;
    } catch (error) {
      // Database infrastructure errors must reach the route's sanitized 500 boundary.
      if (error instanceof ExecutionRecordWorkflowDocumentError) return unavailable(error.reason);
      throw error;
    }
  }

  private storedBinding(run: WorkflowRun): WorkflowSnapshotBinding | null {
    if (run.workflow_snapshot_json == null) return null;
    // The shared parser validates the binding contract; source parsing additionally rejects duplicate keys.
    try { parseExecutionRecordWorkflowJson(run.workflow_snapshot_json, undefined, 64 * 1024); }
    catch (error) {
      if (error instanceof ExecutionRecordWorkflowDocumentError) return { status: 'unresolved', reason: 'IDENTITY_MISMATCH' };
      throw error;
    }
    return parseWorkflowSnapshotBinding(run.workflow_snapshot_json) ?? { status: 'unresolved', reason: 'IDENTITY_MISMATCH' };
  }

  private matchesRun(ref: WorkflowSnapshotRef, run: WorkflowRun): boolean {
    return ref.workflowId === run.workflow_id && (ref.kind === 'deployment'
      ? ref.workflowVersion === run.workflow_version && ref.deployNumber === run.workflow_deploy_number
      : run.workflow_version === -1 && ref.sourceFlowId === run.flow_id);
  }

  private async deployment(run: WorkflowRun): Promise<ExecutionRecordWorkflow> {
    const rows = await this.db.query<Deployment>(
      'SELECT workflow_id, version, deploy_number, spec_json FROM workflow_deploy_history WHERE workflow_id = ? AND deploy_number = ?',
      [run.workflow_id, run.workflow_deploy_number],
    );
    if (!rows.length) return unavailable('SNAPSHOT_MISSING');
    if (rows.length !== 1 || rows[0].workflow_id !== run.workflow_id || rows[0].version !== run.workflow_version
      || rows[0].deploy_number !== run.workflow_deploy_number) return unavailable('IDENTITY_MISMATCH');
    const spec = readExecutionRecordWorkflowSpec(rows[0].spec_json);
    const workflow_yaml = executionRecordWorkflowYaml(spec, run.workflow_id);
    return { workflow_yaml, workflow_snapshot: { status: 'resolved', snapshot: {
      kind: 'deployment', workflowId: run.workflow_id, workflowVersion: rows[0].version,
      deployNumber: rows[0].deploy_number, configDigest: workflowConfigurationDigest(spec),
    } } };
  }

  private saved(run: WorkflowRun): ExecutionRecordWorkflow {
    if (!run.state_json) return unavailable('LEGACY_MISSING');
    const state = parseExecutionRecordWorkflowJson(run.state_json, ['flowId', 'workflowId', 'workflowPin', 'workflowSnapshot'], 8 * 1024 * 1024);
    if (!workflowObject(state)) return unavailable('IDENTITY_MISMATCH');
    if (Object.hasOwn(state, 'flowId') && state.flowId !== run.flow_id
      || Object.hasOwn(state, 'workflowId') && state.workflowId !== run.workflow_id) return unavailable('IDENTITY_MISMATCH');
    const pin = state.workflowPin, spec = state.workflowSnapshot;
    if (pin == null || spec == null) return unavailable('LEGACY_MISSING');
    if (!workflowObject(pin) || !workflowObject(spec) || pin.source !== 'remote-snapshot'
      || pin.workflowId !== run.workflow_id || spec.id !== run.workflow_id) return unavailable('IDENTITY_MISMATCH');
    const commit = typeof pin.workflowDigest === 'string' ? /^remote-snapshot:([a-f0-9]{40,64})$/.exec(pin.workflowDigest)?.[1] : null;
    if (!commit || pin.packDigest !== undefined && typeof pin.packDigest !== 'string') return unavailable('IDENTITY_MISMATCH');
    const workflow_yaml = executionRecordWorkflowYaml(spec, run.workflow_id);
    return { workflow_yaml, workflow_snapshot: { status: 'resolved', snapshot: {
      kind: 'saved', workflowId: run.workflow_id, commit, sourceFlowId: run.flow_id,
      configDigest: workflowConfigurationDigest(spec), ...(typeof pin.packDigest === 'string' ? { packDigest: pin.packDigest } : {}),
    } } };
  }
}
