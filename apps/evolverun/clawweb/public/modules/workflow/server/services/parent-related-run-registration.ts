import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunLinkRepository, type FlowRunLink } from '@avernet/clawweb-shared/server/repositories/flow-run-link-repository';
import { parseWorkflowSnapshotBinding, WorkflowSnapshotRepository } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import type { FlowRunInsert } from '../repositories/flow-run-repository.js';
import { hasTrialFields } from './ordinary-run-request.js';

export type ParentRelationFanout = Readonly<{
  group_execution_id: string;
  item_id: string;
  item_attempt: number;
  dispatch_claim_token: string;
}>;
export type ParentRelation = Readonly<{
  kind: 'subworkflow.v1';
  parent_flow_id: string;
  parent_node_id: string;
  parent_attempt: number;
  fanout?: ParentRelationFanout;
}>;
export type ValidatedParentRelation = ParentRelation;

export class ParentRelationError extends Error {
  constructor(readonly status: 400 | 409 | 503, message: string) { super(message); }
}
const invalid = (message = 'Invalid parent relation') => new ParentRelationError(400, message);
const conflict = (message = 'Parent relation is not authorized') => new ParentRelationError(409, message);
const unavailable = (message = 'Parent relation persistence unavailable') => new ParentRelationError(503, message);
const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const validId = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,255}$/.test(value);
// ClawMind builds composite fanout item IDs from encodeURIComponent'd values
// joined with a literal `/`; percent escapes may also occur (for example `%2F`).
const validItemId = (value: unknown): value is string => typeof value === 'string'
  && value.length > 0 && value.length <= 255 && /^(?:[A-Za-z0-9_.:~\/%-]|%[0-9A-Fa-f]{2})+$/.test(value)
  && !/%(?![0-9A-Fa-f]{2})/.test(value);
const validToken = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 512
  && !/[\s\x00-\x1f\x7f/\\]/u.test(value);
const positiveInt = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0 && Number(value) <= 2_147_483_647;
const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);
const locked = (db: IDatabase): string => db.dbType === 'mysql' || db.dbType === 'zdas' ? ' FOR UPDATE' : '';

function isParentRelationFanout(value: Record<string, unknown>): value is ParentRelationFanout {
  const keys = ['group_execution_id', 'item_id', 'item_attempt', 'dispatch_claim_token'];
  return Object.keys(value).length === keys.length
    && keys.every(key => Object.hasOwn(value, key))
    && validId(value.group_execution_id) && validItemId(value.item_id)
    && positiveInt(value.item_attempt) && validToken(value.dispatch_claim_token);
}

export function parseParentRelation(value: unknown): ParentRelation | null {
  if (!isObject(value)) return null;
  const keys = Object.keys(value);
  const required = ['kind', 'parent_flow_id', 'parent_node_id', 'parent_attempt'];
  if (value.kind !== 'subworkflow.v1' || keys.some(key => !required.includes(key) && key !== 'fanout')
    || required.some(key => !Object.hasOwn(value, key))) return null;
  if (!validId(value.parent_flow_id) || !validId(value.parent_node_id) || !positiveInt(value.parent_attempt)) return null;
  if (!Object.hasOwn(value, 'fanout')) {
    return Object.freeze({ kind: 'subworkflow.v1', parent_flow_id: value.parent_flow_id,
      parent_node_id: value.parent_node_id, parent_attempt: value.parent_attempt });
  }
  if (!isObject(value.fanout)) return null;
  const fanout = value.fanout;
  const fanoutKeys = ['group_execution_id', 'item_id', 'item_attempt', 'dispatch_claim_token'];
  if (!isParentRelationFanout(fanout)) return null;
  return Object.freeze({ kind: 'subworkflow.v1', parent_flow_id: value.parent_flow_id,
    parent_node_id: value.parent_node_id, parent_attempt: value.parent_attempt,
    fanout: Object.freeze({ ...fanout }) });
}

type ParentRun = {
  flow_id: string; workflow_id: string; status: string; state_json: string | null;
  origin_bot_id: string | null; user_id: string | null; origin_session_key: string | null; origin_session_id: string | null;
  workflow_version: number | null; workflow_deploy_number: number | null;
};
function missingWorkflowSnapshotColumn(error: unknown): boolean {
  const message = String(error).toLowerCase();
  return message.includes('workflow_snapshot_json')
    && (message.includes('no such column') || message.includes('unknown column'));
}

type ChildRun = ParentRun & { workflow_version: number | null; workflow_deploy_number: number | null;
  workflow_title: string | null; params_json: string | null; input_json: string | null; node_count: number;
  triggered_by: string | null; identity_key: string | null; current_phase: string | null; credentials_json: string | null;
  plugin_version: string | null; engine: string | null };
type NodeExecution = { id: number | string; flow_id: string; workflow_id: string; node_id: string; status: string; attempt: number };

function runInsertColumns(run: FlowRunInsert): { sql: string; params: unknown[] } {
  return {
    sql: `INSERT INTO flow_runs (flow_id, workflow_id, workflow_title, status, state_json, params_json, input_json, node_count, triggered_by, identity_key, current_phase, started_at, credentials_json, origin_session_key, origin_session_id, origin_bot_id, user_id, plugin_version, engine, workflow_version, workflow_deploy_number, gmt_create, gmt_modified)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    params: [run.flowId, run.workflowId, run.workflowTitle ?? null, run.status, run.stateJson ?? null, run.paramsJson ?? null,
      run.inputJson ?? null, run.nodeCount ?? 0, run.triggeredBy ?? null, run.identityKey ?? null, run.currentPhase ?? null,
      run.startedAt, run.credentialsJson ?? null, run.originSessionKey ?? null, run.originSessionId ?? null,
      run.originBotId ?? null, run.userId ?? null, run.pluginVersion ?? null, run.engine ?? null,
      run.workflowVersion ?? null, run.workflowDeployNumber ?? null, run.startedAt, run.startedAt],
  };
}
async function insertRun(tx: IDatabase, run: FlowRunInsert): Promise<void> {
  const now = tx.dialect.now();
  const statement = runInsertColumns(run);
  statement.params[statement.params.length - 2] = now;
  statement.params[statement.params.length - 1] = now;
  const result = await tx.exec(statement.sql, statement.params);
  if (result.affectedRows !== 1) throw unavailable('Flow run persistence failed');
  await new WorkflowSnapshotRepository(tx).associateRun(run.flowId);
}

function immutableRegistrationMatches(row: ChildRun, run: FlowRunInsert): boolean {
  return row.workflow_id === run.workflowId && row.workflow_title === (run.workflowTitle ?? null)
    // `state_json` and `current_phase` are mutable child run state. A delayed
    // exact registration retry must be able to rejoin after execution advanced.
    && row.params_json === (run.paramsJson ?? null) && row.input_json === (run.inputJson ?? null)
    && row.node_count === (run.nodeCount ?? 0) && row.triggered_by === (run.triggeredBy ?? null)
    && row.identity_key === (run.identityKey ?? null)
    && row.credentials_json === (run.credentialsJson ?? null)
    && row.origin_session_key === (run.originSessionKey ?? null) && row.origin_session_id === (run.originSessionId ?? null)
    && row.origin_bot_id === (run.originBotId ?? null) && row.user_id === (run.userId ?? null)
    && row.plugin_version === (run.pluginVersion ?? null) && row.engine === (run.engine ?? null)
    && row.workflow_version === (run.workflowVersion ?? null) && row.workflow_deploy_number === (run.workflowDeployNumber ?? null);
}
function findNodeExecutor(value: unknown, nodeId: string, seen = new Set<object>()): Record<string, unknown> | null {
  if (!isObject(value) || seen.has(value)) return null;
  seen.add(value);
  if (value.id === nodeId && isObject(value.executor)) return value.executor;
  for (const child of Object.values(value)) {
    if (Array.isArray(child)) for (const item of child) { const found = findNodeExecutor(item, nodeId, seen); if (found) return found; }
    else { const found = findNodeExecutor(child, nodeId, seen); if (found) return found; }
  }
  return null;
}
function parseJsonObject(value: string | null, message: string): Record<string, unknown> {
  if (value === null) throw conflict(message);
  try { const parsed: unknown = JSON.parse(value); if (!isObject(parsed)) throw new Error(); return parsed; }
  catch { throw conflict(message); }
}
function validateIdentity(parent: ParentRun, child: FlowRunInsert, expectedSessionKey: string | null): void {
  if (!parent.origin_bot_id || !parent.user_id || !parent.origin_session_key || !parent.origin_session_id
    || child.originBotId !== parent.origin_bot_id || child.userId !== parent.user_id
    || child.originSessionKey !== expectedSessionKey || child.originSessionId !== parent.origin_session_id) {
    throw conflict('Child identity does not match the persisted parent authority');
  }
}
function checkInvocation(group: Record<string, unknown>, relation: ParentRelation, execution: NodeExecution): void {
  const invocation = group.invocation;
  if (invocation === undefined) return;
  if (!isObject(invocation) || invocation.schemaVersion !== 'subworkflow.single.v1'
    || !positiveInt(invocation.parentAttempt) || !validId(invocation.parentExecutionId)
    || (invocation.onFailure !== undefined && invocation.onFailure !== 'skip')) throw conflict('Invalid persisted single-subworkflow invocation');
  if (invocation.parentAttempt !== relation.parent_attempt || invocation.parentExecutionId !== String(execution.id)) {
    throw conflict('Parent execution invocation does not match the relation');
  }
}
function checkFanout(parent: ParentRun, relation: ParentRelation, child: FlowRunInsert, execution: NodeExecution): { sessionKey: string | null; groupState: Record<string, unknown> | null } {
  const state = parseJsonObject(parent.state_json, 'Parent durable state is unavailable');
  const groups = isObject(state.subworkflowFanouts) ? state.subworkflowFanouts : null;
  const group = groups && groups[relation.parent_node_id];
  if (!relation.fanout) {
    if (group) throw conflict('Fanout group relation is required');
    return { sessionKey: parent.origin_session_key, groupState: null };
  }
  if (!isObject(group) || group.schemaVersion !== 'fanout.v1' || group.kind !== 'subworkflowFanoutState'
    || group.parentFlowId !== parent.flow_id || group.groupNodeId !== relation.parent_node_id
    || group.groupExecutionId !== relation.fanout.group_execution_id || group.status !== 'running'
    || !isObject(group.childWorkflow) || group.childWorkflow.id !== child.workflowId || !Array.isArray(group.children)) {
    throw conflict('No matching durable subworkflow group');
  }
  if (group.deadlineAt !== null && Date.parse(String(group.deadlineAt)) <= Date.now()) throw conflict('Subworkflow group dispatch has expired');
  checkInvocation(group, relation, execution);
  const item = group.children.find(value => isObject(value) && value.itemId === relation.fanout!.item_id);
  if (!item || item.parentFlowId !== parent.flow_id || item.parentNodeId !== relation.parent_node_id
    || item.groupExecutionId !== relation.fanout.group_execution_id || item.attemptNumber !== relation.fanout.item_attempt
    || item.status !== 'dispatch_pending' || item.dispatchClaimToken !== relation.fanout.dispatch_claim_token
    || item.nativeFlowId !== undefined && item.nativeFlowId !== null && item.nativeFlowId !== child.flowId) {
    throw conflict('No matching durable subworkflow dispatch claim');
  }
  return { sessionKey: typeof item.sessionKey === 'string' ? item.sessionKey : parent.origin_session_key, groupState: group };
}
async function validateAuthority(tx: IDatabase, relation: ParentRelation, run: FlowRunInsert): Promise<{
  parent: ParentRun; execution: NodeExecution; link: FlowRunLink; validated: ValidatedParentRelation; groupState: Record<string, unknown> | null;
}> {
  const parentRows = await tx.query<ParentRun>(`SELECT flow_id,workflow_id,status,state_json,origin_bot_id,user_id,origin_session_key,origin_session_id,workflow_version,workflow_deploy_number FROM flow_runs WHERE flow_id = ?${locked(tx)}`, [relation.parent_flow_id]);
  const parent = parentRows[0];
  if (!parent || parent.flow_id !== relation.parent_flow_id || parent.status !== 'running') throw conflict('Parent run is not running');
  const executions = await tx.query<NodeExecution>(`SELECT id,flow_id,workflow_id,node_id,status,attempt FROM node_executions WHERE flow_id = ? AND node_id = ? AND attempt = ? ORDER BY id DESC LIMIT 1${locked(tx)}`, [parent.flow_id, relation.parent_node_id, relation.parent_attempt]);
  const execution = executions[0];
  if (!execution || execution.flow_id !== parent.flow_id || execution.workflow_id !== parent.workflow_id
    || execution.node_id !== relation.parent_node_id || execution.status !== 'running' || execution.attempt !== relation.parent_attempt) {
    throw conflict('Parent node execution is not running');
  }
  const snapshots = new WorkflowSnapshotRepository(tx);
  let storedBinding: Awaited<ReturnType<WorkflowSnapshotRepository['forRun']>>;
  let hasAssociationColumn = true;
  try {
    const snapshotRows = await tx.query<{ workflow_snapshot_json: string | null }>(
      `SELECT workflow_snapshot_json FROM flow_runs WHERE flow_id = ?${locked(tx)}`, [parent.flow_id]);
    storedBinding = snapshotRows[0]?.workflow_snapshot_json
      ? parseWorkflowSnapshotBinding(snapshotRows[0].workflow_snapshot_json) : null;
  } catch (error) {
    if (!missingWorkflowSnapshotColumn(error)) throw error;
    hasAssociationColumn = false;
    storedBinding = null;
  }
  if (!hasAssociationColumn) {
    // Externally provisioned databases may run current code before migration v145;
    // deployment-bound runs still have an immutable publish-history source. Draft
    // or saved snapshots must wait for the association column rather than trust
    // mutable state_json as a substitute registration receipt.
    if (Number(parent.workflow_version ?? 0) <= 0 || Number(parent.workflow_deploy_number ?? 0) <= 0) {
      throw conflict('Parent workflow snapshot is unavailable');
    }
    const resolved = await snapshots.forRun(parent.flow_id);
    storedBinding = resolved;
    if (storedBinding?.status === 'resolved' && storedBinding.snapshot.kind !== 'deployment') {
      storedBinding = null;
    }
  }
  if (!storedBinding || storedBinding.status !== 'resolved') throw conflict('Parent workflow snapshot is unavailable');
  let snapshot;
  try { snapshot = await snapshots.read(storedBinding.snapshot); } catch { throw conflict('Parent workflow snapshot is not immutable'); }
  const executor = findNodeExecutor(snapshot.workflowSpec, relation.parent_node_id);
  if (!executor || executor.type !== 'subworkflow' || executor.workflowId !== run.workflowId) throw conflict('Parent node does not target the child workflow');
  const fanout = checkFanout(parent, relation, run, execution);
  validateIdentity(parent, run, fanout.sessionKey);
  if (!relation.fanout && isObject(executor.fanout)) throw conflict('Fanout relation is required for a fanout node');
  return { parent, execution, link: { childFlowId: run.flowId, parentFlowId: parent.flow_id, parentNodeId: relation.parent_node_id,
    parentAttempt: relation.parent_attempt, childWorkflowId: run.workflowId }, validated: relation, groupState: fanout.groupState };
}

export async function registerParentRelatedRun(db: IDatabase, body: Record<string, unknown>, run: FlowRunInsert,
  relation: ParentRelation): Promise<{ created: boolean; relation: ValidatedParentRelation }> {
  if (hasTrialFields(body)) throw conflict('Trial registration cannot carry a parent relation');
  const perform = () => db.transaction(async tx => {
    const authority = await validateAuthority(tx, relation, run);
    const links = new FlowRunLinkRepository(tx);
    const preexistingLink = await links.findByChild(run.flowId);
    if (preexistingLink && !same(preexistingLink, authority.link)) throw conflict('Flow run link conflicts');
    const existingRows = await tx.query<ChildRun>(`SELECT flow_id,workflow_id,status,state_json,origin_bot_id,user_id,origin_session_key,origin_session_id,
      workflow_version,workflow_deploy_number,workflow_title,params_json,input_json,node_count,triggered_by,identity_key,current_phase,credentials_json,plugin_version,engine
      FROM flow_runs WHERE flow_id = ?${locked(tx)}`, [run.flowId]);
    const existing = existingRows[0];
    if (existing) {
      if (!immutableRegistrationMatches(existing, run)) throw conflict('Flow run registration metadata conflicts');
      if (!preexistingLink || !same(preexistingLink, authority.link)) throw conflict('Flow run link conflicts');
      return { created: false, relation: authority.validated };
    }
    await insertRun(tx, run);
    if (!preexistingLink) await links.insert(authority.link);
    if (authority.groupState && relation.fanout && authority.groupState.children instanceof Array) {
      const nextGroup = structuredClone(authority.groupState);
      const item = (nextGroup.children as unknown[]).find((value: unknown) => isObject(value) && value.itemId === relation.fanout!.item_id) as Record<string, unknown> | undefined;
      if (!item) throw conflict('Fanout dispatch claim disappeared');
      if (item.nativeFlowId !== undefined && item.nativeFlowId !== null && item.nativeFlowId !== run.flowId) throw conflict('Fanout dispatch claim is already bound');
      item.nativeFlowId = run.flowId;
      const parentState = parseJsonObject(authority.parent.state_json, 'Parent durable state is unavailable');
      const groups = isObject(parentState.subworkflowFanouts) ? parentState.subworkflowFanouts : {};
      const currentGroup = groups[relation.parent_node_id];
      if (!isObject(currentGroup) || !same(currentGroup, authority.groupState)) throw conflict('Fanout group changed during registration');
      groups[relation.parent_node_id] = nextGroup;
      parentState.subworkflowFanouts = groups;
      const nextStateJson = JSON.stringify(parentState);
      const statePredicate = tx.dbType === 'mysql' || tx.dbType === 'zdas' ? 'BINARY state_json = BINARY ?' : 'state_json = ?';
      const updated = await tx.exec(`UPDATE flow_runs SET state_json = ? WHERE flow_id = ? AND status = 'running' AND ${statePredicate}`,
        [nextStateJson, authority.parent.flow_id, authority.parent.state_json]);
      if (updated.affectedRows !== 1) throw unavailable('Parent fanout binding persistence failed');
    }
    return { created: true, relation: authority.validated };
  });
  try { return await perform(); }
  catch (error) {
    const code = (error as { code?: unknown } | null)?.code;
    if (code === 'ER_DUP_ENTRY' || code === 'SQLITE_CONSTRAINT_PRIMARYKEY' || code === 'SQLITE_CONSTRAINT_UNIQUE') return perform();
    if (error instanceof ParentRelationError) throw error;
    throw unavailable('Parent-related run registration failed');
  }
}
