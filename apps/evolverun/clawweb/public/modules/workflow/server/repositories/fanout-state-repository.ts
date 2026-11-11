/**
 * Durable fanout Service API, backed exclusively by flow_runs.state_json.
 * Group v1: taskguard's flat, single-attempt subworkflowFanoutState snapshot.
 * Group create uses revision 0 / expectedRevision null; updates use expected + 1.
 * Child create returns { created, run }; checkpoints use _fanoutRevision (initial 1).
 * All persistence errors propagate. No best-effort FlowRunRepository methods here.
 */
import { createHash } from 'node:crypto';
import type { IDatabase, FlowControlAppConfig } from '@avernet/clawweb-shared/server/db';
import { FlowRunLinkRepository } from '@avernet/clawweb-shared/server/repositories/flow-run-link-repository';

type ObjectValue = Record<string, unknown>;
type Child = ObjectValue & {
  parentFlowId: string; parentNodeId: string; groupExecutionId: string; itemId: string;
  itemIndex: number; attemptNumber: number; childFlowId: string; sessionKey?: string;
  stateDirRef?: string; depth: number; params: ObjectValue; status: string;
};
type Group = ObjectValue & {
  parentFlowId: string; groupNodeId: string; groupExecutionId: string; revision: number;
  status: string; deadlineAt: string | null; childWorkflow: ObjectValue; childPublishedVersion?: number;
  invocation?: ObjectValue; children: Child[];
};
type RunRow = { flow_id: string; workflow_id: string; status: string; origin_session_key: string | null; state_json: string | null };
export type FanoutChildRecord = {
  flowId: string; workflowId: string; status: string; sessionKey: string;
  stateJson: ObjectValue; revision: number;
};
export type FanoutChildStart = {
  parentNodeId: string; groupExecutionId: string; itemId: string; attemptNumber: number;
  workflowId: string; sessionKey: string; stateJson: ObjectValue;
  workflowTitle?: string; originBotId?: string; userId?: string;
};
export type FanoutChildCheckpoint = {
  expectedRevision: number; stateJson: ObjectValue; status: string; currentStep?: string;
};
/** Optional host admission settings; absent limits remain finite (five). */
export type FanoutAdmissionConfig = FlowControlAppConfig & {
  perWorkflow?: Record<string, { maxConcurrent?: number }>;
};
export class FanoutStorageError extends Error {
  constructor(public readonly statusCode: 400 | 404 | 409 | 503, message: string, public readonly code?: string) { super(message); }
}
const terminal = new Set(['succeeded', 'failed', 'cancelled', 'canceled', 'timed_out', 'rejected', 'completed', 'aborted']);
const parentActive = new Set(['running', 'waiting', 'pending', 'queued', 'blocked', 'paused', 'suspended', 'created']);
const groupStatuses = new Set(['running', 'cancelling', 'succeeded', 'failed', 'cancelled', 'timed_out']);
const childStatuses = new Set(['queued', 'dispatch_pending', 'dispatch_unknown', 'running', 'cancel_requested',
  'succeeded', 'failed', 'cancelled', 'timed_out', 'rejected']);
const checkpointStatuses = new Set([...parentActive, ...terminal, 'cancelling', 'cancel_requested']);
const maxCasAttempts = 8;
const credentialKeys = new Set(['originCredentials', 'credentials', 'credentialsJson', 'credentials_json']);

function object(value: unknown): value is ObjectValue { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function text(value: unknown): value is string { return typeof value === 'string' && value.trim().length > 0; }
function integer(value: unknown, min = 0): value is number { return Number.isSafeInteger(value) && Number(value) >= min; }
function valid(condition: unknown, message: string): asserts condition {
  if (!condition) throw new FanoutStorageError(400, message);
}
function conflict(condition: unknown, message: string): asserts condition {
  if (!condition) throw new FanoutStorageError(409, message);
}
function timestamp(value: unknown): boolean { return text(value) && Number.isFinite(Date.parse(value)); }
function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, item) => object(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
}
function equal(a: unknown, b: unknown): boolean { return canonical(a) === canonical(b); }
function digest(value: unknown): string { return createHash('sha256').update(canonical(value)).digest('hex'); }
/** ClawMind workflow-dependencies.ts sorts keys lexically, including integer-like keys. */
function canonicalWorkflow(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalWorkflow).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().filter(key => value[key] !== undefined)
    .map(key => `${JSON.stringify(key)}:${canonicalWorkflow(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function workflowDigest(value: unknown): string {
  return `sha256:${createHash('sha256').update(canonicalWorkflow(value), 'utf8').digest('hex')}`;
}
/** Match buildChildFlowState's legacy string-valued parameter boundary. */
function legacyParams(params: ObjectValue): Record<string, string> {
  return Object.fromEntries(Object.entries(params).map(([key, value]) => [key, typeof value === 'string' ? value : JSON.stringify(value)]));
}
/** Credential-bearing transport fields are neither stored nor returned by this API. */
function clean<T>(value: T): T {
  return JSON.parse(JSON.stringify(value, (key, item) => credentialKeys.has(key) ? undefined : item)) as T;
}
// Inside immutable workflow specs, credential-named fields are permitted only
// as unresolved, whole-value references. Literal/mixed values fail closed:
// deleting one would change the spec digest and retaining it could leak secrets.
function safeCredentialTemplate(value: unknown): boolean {
  if (value === null) return true;
  if (typeof value === 'string') return /^\{\{\s*(?:params|item|nodeOutput|input\.params)(?:\.[\w@-]+(?:\[\d+\])*)+\s*\}\}$/.test(value);
  if (Array.isArray(value)) return value.every(safeCredentialTemplate);
  return object(value) && Object.values(value).every(safeCredentialTemplate);
}
function safeWorkflowSnapshot(value: unknown): unknown {
  const snapshot: unknown = JSON.parse(JSON.stringify(value));
  const visit = (item: unknown): void => {
    if (Array.isArray(item)) { item.forEach(visit); return; }
    if (!object(item)) return;
    for (const [key, child] of Object.entries(item)) {
      if (credentialKeys.has(key) && !safeCredentialTemplate(child)) {
        throw new FanoutStorageError(400, 'Workflow snapshot contains unsafe credential values', 'UNSAFE_WORKFLOW_CREDENTIALS');
      }
      visit(child);
    }
  };
  visit(snapshot);
  return snapshot;
}
/** Preserve validated immutable specs verbatim while cleaning other group fields. */
function cleanGroup(value: ObjectValue): ObjectValue {
  const spec = value.childWorkflow === undefined ? undefined : safeWorkflowSnapshot(value.childWorkflow);
  const resolvedSpec = object(value.childResolved) && value.childResolved.spec !== undefined
    ? safeWorkflowSnapshot(value.childResolved.spec) : undefined;
  const group = clean(value);
  if (spec !== undefined) group.childWorkflow = spec;
  if (resolvedSpec !== undefined && object(group.childResolved)) group.childResolved.spec = resolvedSpec;
  return group;
}
/** Preserve the managed child's immutable spec; only trusted durable groups survive checkpoints. */
function cleanChildState(value: ObjectValue, preserveGroups = false): ObjectValue {
  const spec = value.workflowSnapshot === undefined ? undefined : safeWorkflowSnapshot(value.workflowSnapshot);
  const state = clean(value);
  if (spec !== undefined) state.workflowSnapshot = spec;
  if (preserveGroups && object(value.subworkflowFanouts)) {
    state.subworkflowFanouts = Object.fromEntries(Object.entries(value.subworkflowFanouts)
      .map(([key, group]) => [key, object(group) ? cleanGroup(group) : clean(group)]));
  }
  return state;
}
function parseState(raw: string | null): ObjectValue {
  if (raw === null) return {};
  const parsed: unknown = JSON.parse(raw);
  if (!object(parsed)) throw new Error('Invalid durable run state');
  return parsed;
}
function groupsOf(state: ObjectValue): ObjectValue {
  if (!Object.hasOwn(state, 'subworkflowFanouts')) return {};
  if (!object(state.subworkflowFanouts)) throw new Error('Invalid durable fanout map');
  return state.subworkflowFanouts;
}
function errorValue(value: unknown): boolean {
  return value === null || (object(value) && text(value.category) && text(value.code) && typeof value.message === 'string');
}
function validateGroup(value: unknown, parentFlowId: string, nodeId: string): asserts value is Group {
  valid(object(value), 'group must be an object');
  valid(value.schemaVersion === 'fanout.v1' && value.kind === 'subworkflowFanoutState', 'Unsupported fanout schema');
  valid(value.parentFlowId === parentFlowId && value.groupNodeId === nodeId && text(value.groupExecutionId), 'Invalid group identity');
  valid(integer(value.revision) && groupStatuses.has(String(value.status)), 'Invalid group revision or status');
  const singleInvocation = value.invocation !== undefined;
  valid(timestamp(value.createdAt) && (singleInvocation ? (value.deadlineAt === null || timestamp(value.deadlineAt)) : timestamp(value.deadlineAt)), 'Invalid group timestamps');
  for (const key of ['sealedAt', 'cancelRequestedAt', 'cancelDeadlineAt']) valid(value[key] === null || timestamp(value[key]), `Invalid ${key}`);
  const config = value.config;
  valid(object(config) && config.schemaVersion === 'fanout.v1', 'Invalid fanout config');
  valid(config.successPolicy === 'all_done' && config.finishMode === 'wait_all' && config.resultPolicy === 'collect_all', 'Unsupported fanout policy');
  valid((text(config.items) || (Array.isArray(config.items) && config.items.every(object)))
    && Array.isArray(config.itemIdFrom) && config.itemIdFrom.length > 0 && config.itemIdFrom.every(text), 'Invalid fanout items');
  valid(integer(config.concurrency, 1)
    && (singleInvocation
      ? (config.groupTimeoutSeconds === undefined || (typeof config.groupTimeoutSeconds === 'number' && Number.isFinite(config.groupTimeoutSeconds) && config.groupTimeoutSeconds > 0))
      : (typeof config.groupTimeoutSeconds === 'number' && Number.isFinite(config.groupTimeoutSeconds) && config.groupTimeoutSeconds > 0))
    && (config.cancelGraceSeconds === undefined || (typeof config.cancelGraceSeconds === 'number'
    && Number.isFinite(config.cancelGraceSeconds) && config.cancelGraceSeconds > 0)), 'Invalid fanout limits');
  valid(value.childPublishedVersion === undefined || integer(value.childPublishedVersion, 1), 'Invalid child published version');
  if (value.invocation !== undefined) {
    valid(object(value.invocation), 'Invalid single-subworkflow invocation');
    const invocationKeys = Object.keys(value.invocation);
    valid(invocationKeys.every(key => ['schemaVersion', 'parentAttempt', 'parentExecutionId', 'onFailure'].includes(key))
      && invocationKeys.includes('schemaVersion') && invocationKeys.includes('parentAttempt')
      && invocationKeys.includes('parentExecutionId'), 'Invalid single-subworkflow invocation');
    valid(value.invocation.schemaVersion === 'subworkflow.single.v1' && integer(value.invocation.parentAttempt, 1)
      && text(value.invocation.parentExecutionId) && (value.invocation.onFailure === undefined || value.invocation.onFailure === 'skip'),
      'Invalid single-subworkflow invocation');
  }
  valid(object(value.childWorkflow) && text(value.childWorkflow.id)
    && Array.isArray(value.childWorkflow.nodes) && value.childWorkflow.nodes.every(node => object(node) && text(node.id) && object(node.executor)), 'Invalid child workflow snapshot');
  if (value.childResolved !== undefined) {
    const resolved = value.childResolved;
    valid(object(resolved) && resolved.id === value.childWorkflow.id && object(resolved.spec)
      && resolved.spec.id === value.childWorkflow.id && workflowDigest(resolved.spec) === workflowDigest(value.childWorkflow)
      && text(resolved.digest) && typeof resolved.absolutePath === 'string'
      && object(resolved.pack) && ['id', 'version', 'root', 'digest'].every(key => typeof resolved.pack[key] === 'string')
      && object(resolved.source) && ['workspace-pack', 'configured-pack', 'remote-snapshot', 'db', 'deploy-history'].includes(String(resolved.source.kind))
      && (!['workspace-pack', 'configured-pack'].includes(String(resolved.source.kind)) || text(resolved.source.root))
      && (resolved.workflowVersion === undefined || (typeof resolved.workflowVersion === 'number' && Number.isFinite(resolved.workflowVersion)))
      && (resolved.deployNumber === undefined || (typeof resolved.deployNumber === 'number' && Number.isFinite(resolved.deployNumber)))
      && (resolved.tagName === undefined || typeof resolved.tagName === 'string'), 'Invalid resolved child snapshot');
  }
  if (value.childRelease !== undefined) {
    const release = value.childRelease;
    valid(object(release) && integer(release.publishedVersion, 1) && release.publishedVersion === value.childPublishedVersion
      && text(release.releaseId) && typeof release.digest === 'string' && /^sha256:[a-f0-9]{64}$/.test(release.digest)
      && release.digest === workflowDigest(value.childWorkflow), 'Invalid child release identity');
  }
  valid(text(value.inputDigest) && Array.isArray(value.children), 'Invalid group input or children');
  const childIds = new Set<string>();
  const itemIds = new Set<string>();
  const nativeIds = new Set<string>();
  for (const [index, child] of value.children.entries()) {
    valid(object(child) && child.parentFlowId === parentFlowId && child.parentNodeId === nodeId
      && child.groupExecutionId === value.groupExecutionId, 'Invalid child parent identity');
    valid(text(child.itemId) && child.itemIndex === index && child.attemptNumber === 1 && text(child.childFlowId)
      && (child.sessionKey === undefined || text(child.sessionKey)) && (child.stateDirRef === undefined || text(child.stateDirRef))
      && (child.nativeFlowId === undefined || child.nativeFlowId === null || text(child.nativeFlowId))
      && (!Object.hasOwn(child, 'dispatchClaimToken') || text(child.dispatchClaimToken))
      && integer(child.depth, 1), 'Invalid v1 child attempt');
    valid(!childIds.has(child.childFlowId) && !itemIds.has(child.itemId), 'Duplicate child identity');
    childIds.add(child.childFlowId); itemIds.add(child.itemId);
    if (text(child.nativeFlowId)) {
      valid(!nativeIds.has(child.nativeFlowId), 'Duplicate native child identity');
      nativeIds.add(child.nativeFlowId);
    }
    valid(object(child.item) && object(child.params) && childStatuses.has(String(child.status))
      && (child.result === null || object(child.result)) && errorValue(child.error), 'Invalid child snapshot');
  }
  valid(Array.isArray(value.cleanupPendingChildIds) && value.cleanupPendingChildIds.every(id => text(id) && childIds.has(id)), 'Invalid cleanup child ids');
  valid(errorValue(value.error), 'Invalid group error');
  if (terminal.has(String(value.status))) {
    const result = value.result;
    valid(value.sealedAt !== null && object(result) && result.schemaVersion === 'fanout.v1'
      && result.kind === 'subworkflowFanoutResult' && result.parentFlowId === parentFlowId
      && result.groupNodeId === nodeId && result.groupExecutionId === value.groupExecutionId
      && result.groupRevision === value.revision && result.childWorkflowId === value.childWorkflow.id
      && result.childPublishedVersion === value.childPublishedVersion
      && result.status === value.status && typeof result.complete === 'boolean' && text(result.businessOutcome)
      && result.successPolicy === 'all_done' && result.finishMode === 'wait_all' && result.resultPolicy === 'collect_all'
      && object(result.counts) && Array.isArray(result.items) && errorValue(result.error), 'Invalid sealed group result');
    for (const key of ['total', 'successCount', 'failedCount', 'cancelledCount', 'timedOutCount', 'rejectedCount', 'pendingCount']) {
      valid(integer(result[key]) && result[key] === result.counts[key], 'Invalid result counts');
    }
    valid(result.items.length === value.children.length && result.items.every((item, i) => object(item)
      && item.itemId === value.children[i].itemId && item.childFlowId === value.children[i].childFlowId
      && item.itemIndex === i && item.attemptNumber === 1 && childStatuses.has(String(item.status))
      && object(item.item) && (item.result === null || object(item.result)) && errorValue(item.error)), 'Invalid result items');
  } else valid(value.sealedAt === null && value.result === null, 'Unsealed group cannot have a result');
}
function storedGroup(state: ObjectValue, parentFlowId: string, nodeId: string): Group | null {
  const map = groupsOf(state);
  if (!Object.hasOwn(map, nodeId)) return null;
  const g = map[nodeId];
  try { validateGroup(g, parentFlowId, nodeId); }
  catch { throw new Error('Invalid durable fanout snapshot'); }
  return g;
}
function frozenGroup(g: Group): unknown {
  return { parentFlowId: g.parentFlowId, groupNodeId: g.groupNodeId, groupExecutionId: g.groupExecutionId,
    createdAt: g.createdAt, deadlineAt: g.deadlineAt, config: g.config, childWorkflow: g.childWorkflow, childPublishedVersion: g.childPublishedVersion, invocation: g.invocation, childRelease: g.childRelease, childResolved: g.childResolved, inputDigest: g.inputDigest,
    children: g.children.map(({ status: _status, result: _result, error: _error, nativeFlowId: _nativeFlowId, dispatchClaimToken: _dispatchClaimToken, ...identity }) => identity) };
}
/** Compare request-key hashes exactly as the ordinary association store does.
 * Already persisted conflicts may advance unchanged (including cancellation),
 * but no write may introduce or enlarge an ambiguity. */
function assertNoNewKeyConflicts(state: ObjectValue, nodeId: string, current: Group | null, next: Group): void {
  const hashKey = (key: string) => createHash('sha256').update(key, 'utf8').digest('hex');
  const counts = (children: Child[]) => {
    const result = new Map<string, number>();
    for (const child of children) {
      const hash = hashKey(child.childFlowId);
      result.set(hash, (result.get(hash) ?? 0) + 1);
    }
    return result;
  };
  const others = new Map<string, number>();
  for (const [otherNode, value] of Object.entries(groupsOf(state))) {
    if (otherNode === nodeId) continue;
    if (!object(value) || !Array.isArray(value.children)) throw new Error('Invalid durable fanout snapshot');
    // The approved attempts layout predates ordinary associations and has no
    // flat request keys. Unknown malformed layouts must not be silently skipped.
    if (value.config === undefined) {
      if (!value.children.every(child => object(child) && Array.isArray(child.attempts))) throw new Error('Invalid durable fanout snapshot');
      continue;
    }
    for (const child of value.children) {
      if (!object(child) || !text(child.childFlowId)) throw new Error('Invalid durable fanout snapshot');
      const hash = hashKey(child.childFlowId);
      others.set(hash, (others.get(hash) ?? 0) + 1);
    }
  }
  const before = counts(current?.children ?? []);
  for (const [hash, after] of counts(next.children)) {
    const prior = before.get(hash) ?? 0;
    const other = others.get(hash) ?? 0;
    const conflicts = (count: number) => count * other + count * (count - 1) / 2;
    conflict(conflicts(after) <= conflicts(prior), 'Fanout request key conflicts with another item or group');
  }
}
function mergeMirror(current: ObjectValue, incoming: ObjectValue): ObjectValue {
  const { subworkflowFanouts: _untrustedGroups, ...rest } = incoming;
  if (Object.hasOwn(current, 'fanoutOrigin')) {
    conflict(!Object.hasOwn(incoming, 'fanoutOrigin') || equal(current.fanoutOrigin, incoming.fanoutOrigin),
      'Fanout origin is immutable');
    // Ordinary fanout children retain their registered origin even when the engine omits it in a mirror.
    rest.fanoutOrigin = current.fanoutOrigin;
  }
  return Object.hasOwn(current, 'subworkflowFanouts') ? { ...rest, subworkflowFanouts: groupsOf(current) } : rest;
}

export class FanoutStateRepository {
  constructor(private readonly db: IDatabase, private readonly admissionConfig?: FanoutAdmissionConfig) {}

  withAdmissionConfig(config?: FanoutAdmissionConfig): FanoutStateRepository {
    return new FanoutStateRepository(this.db, config);
  }
  private deadlineGuard(deadlineAt?: string | null): { sql: string; params: unknown[] } {
    if (deadlineAt === undefined || deadlineAt === null) return { sql: '', params: [] };
    // Evaluate time in the mutation, not before a lock/connection wait. Parent
    // (and existing child) row locks are acquired before this statement starts.
    const now = this.db.dbType === 'sqlite'
      ? "(CAST(strftime('%s', 'now') AS INTEGER) * 1000 + CAST(substr(strftime('%f', 'now'), 4, 3) AS INTEGER))"
      : '(UNIX_TIMESTAMP(CURRENT_TIMESTAMP(3)) * 1000)';
    return { sql: ` AND ${now} < ?`, params: [Date.parse(deadlineAt)] };
  }
  private async admitChild(tx: IDatabase, workflowId: string): Promise<void> {
    const cap = this.admissionConfig?.perWorkflow?.[workflowId]?.maxConcurrent
      ?? this.admissionConfig?.perWorkflow?.default?.maxConcurrent ?? 5;
    if (!integer(cap, 1)) throw new FanoutStorageError(503, 'Fanout capacity must be a positive finite integer', 'INVALID_CAPACITY_CONFIG');
    const published = await tx.query<{ version: number | null }>('SELECT version FROM workflow_specs WHERE workflow_id = ?', [workflowId]);
    if (!published.length || !integer(Number(published[0].version), 1)) {
      throw new FanoutStorageError(409, 'A lockable published workflow is required', 'WORKFLOW_NOT_PUBLISHED');
    }
    const terminalStates = [...terminal];
    const rows = await tx.query<{ count: number | string }>(`SELECT COUNT(*) AS count FROM flow_runs
      WHERE workflow_id = ? AND status NOT IN (${terminalStates.map(() => '?').join(', ')})
        AND JSON_EXTRACT(state_json, '$._fanoutIdentity.parentFlowId') IS NOT NULL`, [workflowId, ...terminalStates]);
    const count = Number(rows[0]?.count);
    if (!integer(count)) throw new Error('Invalid durable admission count');
    if (count >= cap) throw new FanoutStorageError(409, 'Fanout workflow capacity is full; retry the same child ID', 'CAPACITY_BUSY');
  }

  private available(): void {
    if (this.db.dbType === 'noop') throw new FanoutStorageError(503, 'Database not configured');
  }
  private stateGuard(raw: string | null): { sql: string; params: unknown[] } {
    if (raw === null) return { sql: 'state_json IS NULL', params: [] };
    // MySQL TEXT collation must not weaken a byte-for-byte JSON CAS.
    const sql = this.db.dbType === 'mysql' || this.db.dbType === 'zdas' ? 'BINARY state_json = BINARY ?' : 'state_json = ?';
    return { sql, params: [raw] };
  }
  private async read(flowId: string, db = this.db, current = false): Promise<RunRow | null> {
    this.available();
    const lock = current && (db.dbType === 'mysql' || db.dbType === 'zdas') ? ' FOR UPDATE' : '';
    const rows = await db.query<RunRow>(`SELECT flow_id, workflow_id, status, origin_session_key, state_json FROM flow_runs WHERE flow_id = ?${lock}`, [flowId]);
    return rows[0] ?? null;
  }
  private async require(flowId: string, db = this.db, current = false): Promise<RunRow> {
    const row = await this.read(flowId, db, current);
    if (!row) throw new FanoutStorageError(404, 'Flow run not found');
    return row;
  }

  async getGroup(parentFlowId: string, nodeId: string): Promise<{ group: ObjectValue | null; parentStatus: string }> {
    const row = await this.require(parentFlowId);
    const group = storedGroup(parseState(row.state_json), parentFlowId, nodeId);
    return { group: group && cleanGroup(group), parentStatus: row.status };
  }

  async saveGroup(parentFlowId: string, nodeId: string, input: {
    group: unknown; expectedRevision: number | null; parentState?: ObjectValue;
  }): Promise<{ group: ObjectValue; parentStatus: string }> {
    valid(object(input) && object(input.group), 'group is required');
    valid(!['__proto__', 'constructor', 'prototype'].includes(nodeId), 'Invalid node id');
    valid(input.expectedRevision === null || integer(input.expectedRevision), 'Invalid expectedRevision');
    valid(input.parentState === undefined || object(input.parentState), 'Invalid parentState');
    if (Array.isArray(input.group.children)) {
      for (const child of input.group.children) {
        valid(!object(child) || !Object.hasOwn(child, 'dispatchClaimToken') || text(child.dispatchClaimToken), 'Invalid dispatch claim token');
      }
    }
    const next = cleanGroup(input.group);
    validateGroup(next, parentFlowId, nodeId);
    valid(next.revision === (input.expectedRevision === null ? 0 : input.expectedRevision + 1), 'revision must advance by exactly one');
    // Serialize claims with managed creation and first start on the parent row.
    // On MySQL, locking before the deadline statement also avoids using its
    // statement-start timestamp from before a row-lock wait.
    const lockBeforeWrite = next.status === 'succeeded'
      || next.children.some(child => Object.hasOwn(child, 'dispatchClaimToken'));
    const persist = async (db: IDatabase) => {
      if (lockBeforeWrite) await db.exec('UPDATE flow_runs SET flow_id = flow_id WHERE flow_id = ?', [parentFlowId]);
      for (let attempt = 0; attempt < maxCasAttempts; attempt++) {
        // A locking read sees current data even inside a caller's existing
        // MySQL repeatable-read transaction with an older snapshot.
        const lockingRead = lockBeforeWrite && (db.dbType === 'mysql' || db.dbType === 'zdas');
        const row = await this.require(parentFlowId, db, lockBeforeWrite);
        const state = parseState(row.state_json);
        const current = storedGroup(state, parentFlowId, nodeId);
        conflict(current ? current.revision === input.expectedRevision : input.expectedRevision === null, 'Group revision conflict');
        let newClaimIndex = -1;
        if (!current) conflict(next.children.every(child => !Object.hasOwn(child, 'dispatchClaimToken')), 'New groups cannot contain dispatch claims');
        if (current) {
          conflict(current.groupExecutionId === next.groupExecutionId && equal(frozenGroup(current), frozenGroup(next)), 'Group launch identity conflict');
          conflict(!terminal.has(current.status), 'Group is terminal');
          current.children.forEach((child, index) => {
            const incoming = next.children[index];
            if (!terminal.has(child.status) && child.status !== 'queued') {
              conflict(incoming.status !== 'queued', 'Dispatched child cannot return to queued');
            }
            if (text(child.nativeFlowId)) conflict(child.nativeFlowId === incoming.nativeFlowId, 'Native child identity is immutable');
            if (terminal.has(child.status)) conflict(equal(child, incoming), 'Terminal child snapshot is immutable');
            if (Object.hasOwn(child, 'dispatchClaimToken')) {
              conflict(child.dispatchClaimToken === incoming.dispatchClaimToken, 'Dispatch claim is immutable');
            } else if (Object.hasOwn(incoming, 'dispatchClaimToken')) {
              conflict(newClaimIndex === -1 && current.status === 'running' && child.status === 'queued'
                && (child.nativeFlowId === undefined || child.nativeFlowId === null), 'Child is not eligible for a dispatch claim');
              newClaimIndex = index;
            }
          });
          if (newClaimIndex !== -1) {
            // Admission is one atomic transition. Recompute the cleanup list as
            // the collector does, without changing any other child or group data.
            const children = current.children.map((child, index) => index === newClaimIndex
              ? { ...child, status: 'dispatch_pending', dispatchClaimToken: next.children[index].dispatchClaimToken } : child);
            const cleanupPendingChildIds = children.filter(child => child.status !== 'queued' && !terminal.has(child.status))
              .map(child => child.childFlowId);
            conflict(row.status === 'running' && equal(next, { ...current, revision: next.revision, children, cleanupPendingChildIds }),
              'Dispatch claim must be isolated and parent must be running');
            // Any row with this id could represent an existing managed child;
            // fail closed even for foreign or malformed rows. Parent is locked
            // first, matching managed creation/checkpoint lock ordering.
            const childRows = await db.query<{ flow_id: string }>(
              `SELECT flow_id FROM flow_runs WHERE flow_id = ?${lockingRead ? ' FOR UPDATE' : ''}`,
              [current.children[newClaimIndex].childFlowId]);
            conflict(childRows.length === 0, 'Child already exists; dispatch claim is not authorized');
          }
          if (current.status === 'cancelling') conflict(next.status !== 'running' && next.status !== 'succeeded', 'Cancellation cannot be reversed');
          if (current.cancelRequestedAt !== null) conflict(equal(current.cancelRequestedAt, next.cancelRequestedAt)
            && equal(current.cancelDeadlineAt, next.cancelDeadlineAt), 'Cancellation deadline is immutable');
        }
        assertNoNewKeyConflicts(state, nodeId, current, next);
        conflict(!['running', 'succeeded'].includes(next.status) || parentActive.has(row.status), 'Parent is cancelled or terminal');
        const mirrorEmpty = Object.keys(state).every(key => key === 'subworkflowFanouts');
        const base = newClaimIndex === -1 && mirrorEmpty && input.parentState ? clean(input.parentState) : state;
        const merged = { ...base, subworkflowFanouts: { ...groupsOf(state), [nodeId]: next } };
        const guard = this.stateGuard(row.state_json);
        const deadline = this.deadlineGuard(next.status === 'succeeded' || newClaimIndex !== -1 ? next.deadlineAt : undefined);
        const result = await db.exec(`UPDATE flow_runs SET state_json = ? WHERE flow_id = ? AND ${guard.sql} AND status = ?${deadline.sql}`,
          [JSON.stringify(merged), parentFlowId, ...guard.params, row.status, ...deadline.params]);
        if (result.affectedRows > 0) return { group: next, parentStatus: row.status };
      }
      throw new FanoutStorageError(409, 'Concurrent parent state update or expired deadline');
    };
    return lockBeforeWrite ? this.db.transaction(persist) : persist(this.db);
  }

  /** Legacy mirror writes cannot author, delete, or rewind authoritative groups. */
  async updateMirror(flowId: string, stateJson: string): Promise<boolean> {
    const incoming = parseState(stateJson);
    conflict(!Object.hasOwn(incoming, '_fanoutIdentity') && !Object.hasOwn(incoming, '_fanoutRevision'), 'Managed child checkpoints require revision CAS');
    for (let attempt = 0; attempt < maxCasAttempts; attempt++) {
      const row = await this.read(flowId);
      if (!row) return false;
      const current = parseState(row.state_json);
      conflict(!Object.hasOwn(current, '_fanoutIdentity'), 'Managed child checkpoints require revision CAS');
      const next = JSON.stringify(mergeMirror(current, incoming));
      if (next === row.state_json) return true;
      const guard = this.stateGuard(row.state_json);
      const result = await this.db.exec(`UPDATE flow_runs SET state_json = ? WHERE flow_id = ? AND ${guard.sql}`, [next, flowId, ...guard.params]);
      if (result.affectedRows > 0) return true;
    }
    throw new FanoutStorageError(409, 'Concurrent mirror update');
  }

  private childRecord(row: RunRow, parentFlowId: string): FanoutChildRecord {
    const state = parseState(row.state_json);
    const sanitized = cleanChildState(state, true);
    conflict(object(state.subworkflowMeta) && state.subworkflowMeta.parentFlowId === parentFlowId, 'Child parent identity conflict');
    if (!integer(state._fanoutRevision, 1) || !object(state._fanoutIdentity) || typeof state._fanoutStarted !== 'boolean') throw new Error('Invalid durable child checkpoint');
    const identity = state._fanoutIdentity;
    conflict(identity.parentFlowId === parentFlowId && identity.childFlowId === row.flow_id
      && identity.workflowId === row.workflow_id && identity.sessionKey === row.origin_session_key, 'Child launch identity conflict');
    this.validateChildState(state, identity);
    return { flowId: row.flow_id, workflowId: row.workflow_id, status: row.status, sessionKey: row.origin_session_key!,
      stateJson: sanitized, revision: state._fanoutRevision };
  }

  async getChild(parentFlowId: string, childFlowId: string): Promise<FanoutChildRecord> {
    return this.childRecord(await this.require(childFlowId), parentFlowId);
  }

  private validateChildState(state: ObjectValue, identity: ObjectValue): void {
    const meta = state.subworkflowMeta;
    conflict(state.workflowId === identity.workflowId && object(meta) && meta.parentFlowId === identity.parentFlowId
      && meta.parentNodeId === identity.parentNodeId && meta.depth === identity.depth, 'Child state identity conflict');
    conflict(object(state.workflowSnapshot) && digest(state.workflowSnapshot) === identity.specDigest
      && object(state.params) && digest(state.params) === identity.paramsDigest, 'Child spec or input conflict');
    for (const key of ['groupExecutionId', 'itemId', 'itemIndex', 'attemptNumber', 'childFlowId', 'sessionKey', 'stateDirRef']) {
      if (Object.hasOwn(meta, key)) conflict(meta[key] === identity[key], 'Child metadata identity conflict');
    }
    if (Object.hasOwn(state, '_fanoutIdentity')) conflict(equal(state._fanoutIdentity, identity), 'Child identity is immutable');
  }

  async startOrGetChild(parentFlowId: string, childFlowId: string, input: FanoutChildStart): Promise<{ created: boolean; run: FanoutChildRecord }> {
    this.available();
    valid(object(input), 'Child start body is required');
    for (const key of ['parentNodeId', 'groupExecutionId', 'itemId', 'workflowId', 'sessionKey']) valid(text(input[key]), `Invalid ${key}`);
    valid(input.attemptNumber === 1 && object(input.stateJson), 'Invalid child attempt or stateJson');
    for (const key of ['workflowTitle', 'originBotId', 'userId']) valid(input[key] === undefined || typeof input[key] === 'string', `Invalid ${key}`);
    const initial = cleanChildState(input.stateJson);
    valid(initial._fanoutStarted === undefined || initial._fanoutStarted === false, 'A new child cannot be pre-claimed');
    valid(initial.subworkflowFanouts === undefined || (object(initial.subworkflowFanouts)
      && Object.keys(initial.subworkflowFanouts).length === 0), 'Nested fanouts require group CAS');
    initial._fanoutStarted = false;
    valid(object(initial.workflowSnapshot) && object(initial.params) && object(initial.subworkflowMeta), 'Child state requires workflowSnapshot, params and subworkflowMeta');
    valid(initial._fanoutRevision === undefined || initial._fanoutRevision === 1, 'Invalid initial child revision');
    const { _fanoutRevision: _revision, _fanoutIdentity: _identity, ...launchState } = initial;
    // Workflow lock is first: MySQL's repeatable-read snapshot must be taken
    // AFTER admission serialization, otherwise a queued start can count stale slots.
    // The parent lock then fences cancellation; flow_id uniquely fences duplicates.
    return this.db.transaction(async tx => {
      await tx.exec('UPDATE workflow_specs SET workflow_id = workflow_id WHERE workflow_id = ?', [input.workflowId]);
      await tx.exec('UPDATE flow_runs SET flow_id = flow_id WHERE flow_id = ?', [parentFlowId]);
      const parent = await this.require(parentFlowId, tx, true);
      const g = storedGroup(parseState(parent.state_json), parentFlowId, input.parentNodeId);
      conflict(g && g.groupExecutionId === input.groupExecutionId, 'No matching durable group');
      const child = g.children.find(c => c.childFlowId === childFlowId);
      conflict(child && child.parentNodeId === input.parentNodeId && child.itemId === input.itemId
        && child.attemptNumber === input.attemptNumber && child.sessionKey === input.sessionKey
        && g.childWorkflow.id === input.workflowId, 'No matching durable child attempt');
      const params = legacyParams(child.params);
      const identity = {
        parentFlowId, parentNodeId: input.parentNodeId, groupExecutionId: g.groupExecutionId,
        itemId: child.itemId, itemIndex: child.itemIndex, attemptNumber: child.attemptNumber, childFlowId,
        workflowId: input.workflowId, sessionKey: child.sessionKey, stateDirRef: child.stateDirRef, depth: child.depth,
        specDigest: digest(g.childWorkflow), paramsDigest: digest(params), rawParamsDigest: digest(child.params), startDigest: digest(launchState),
        workflowTitle: input.workflowTitle ?? null, originBotId: input.originBotId ?? null, userId: input.userId ?? null,
      };
      this.validateChildState(initial, identity);
      // Persist the trusted child->parent link in the same transaction as the child run.
      const links = new FlowRunLinkRepository(tx);
      // The start contract does not know the parent node's execution attempt.
      const existingLink = await links.findByChild(childFlowId);
      const link = {
        childFlowId,
        parentFlowId,
        parentNodeId: input.parentNodeId,
        parentAttempt: null,
        childWorkflowId: input.workflowId,
      };
      conflict(existingLink === null || equal(existingLink, link), 'Child link already has a different parent');
      const existing = await this.read(childFlowId, tx, true);
      if (existing) {
        const run = this.childRecord(existing, parentFlowId);
        conflict(equal(run.stateJson._fanoutIdentity, identity), 'Child ID already has a different launch identity');
        if (existingLink === null) await links.insert(link);
        return { created: false, run };
      }
      // A dispatch claim belongs to the ordinary launcher, never this legacy
      // managed-child creation path. Existing children remain readable above.
      conflict(!Object.hasOwn(child, 'dispatchClaimToken'), 'Claimed child cannot use managed creation');
      conflict(parentActive.has(parent.status) && g.status === 'running'
        && ['dispatch_pending', 'dispatch_unknown', 'running'].includes(child.status), 'Child creation is no longer authorized');
      await this.admitChild(tx, input.workflowId);
      const deadline = this.deadlineGuard(g.deadlineAt);
      const state = { ...launchState, _fanoutIdentity: identity, _fanoutRevision: 1 };
      const now = tx.dialect.now();
      try {
        const inserted = await tx.exec(`INSERT INTO flow_runs (flow_id, workflow_id, workflow_title, status, state_json,
          params_json, node_count, started_at, origin_session_key, origin_bot_id, user_id, gmt_create, gmt_modified)
          SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE 1 = 1${deadline.sql}`,
        [childFlowId, input.workflowId, input.workflowTitle ?? null, 'running', JSON.stringify(state),
          JSON.stringify(params), (g.childWorkflow.nodes as unknown[]).length, Math.floor(Date.now() / 1000),
          child.sessionKey, input.originBotId ?? null, input.userId ?? null, now, now, ...deadline.params]);
        conflict(inserted.affectedRows > 0, 'Fanout deadline has expired');
      } catch (error) {
        // Only a proven unique collision is a domain conflict. Disk/connection/trigger
        // errors must retain their failure, not become a successful duplicate lookup.
        const code = (error as { code?: string }).code;
        if (code === 'SQLITE_CONSTRAINT_UNIQUE' || code === 'SQLITE_CONSTRAINT_PRIMARYKEY' || code === 'ER_DUP_ENTRY') {
          throw new FanoutStorageError(409, 'Child ID already exists');
        }
        throw error;
      }
      if (existingLink === null) await links.insert(link);
      return { created: true, run: this.childRecord(await this.require(childFlowId, tx), parentFlowId) };
    });
  }

  async checkpointChild(parentFlowId: string, childFlowId: string, input: FanoutChildCheckpoint): Promise<FanoutChildRecord> {
    valid(object(input) && integer(input.expectedRevision, 1) && input.expectedRevision < Number.MAX_SAFE_INTEGER
      && object(input.stateJson), 'Invalid checkpoint revision or state');
    valid(checkpointStatuses.has(input.status), 'Invalid checkpoint status');
    valid(input.currentStep === undefined || typeof input.currentStep === 'string', 'Invalid currentStep');
    const incoming = cleanChildState(input.stateJson);
    valid(incoming._fanoutStarted === undefined || typeof incoming._fanoutStarted === 'boolean', 'Invalid child started flag');
    this.available();
    return this.db.transaction(async tx => {
      // A false -> true execution claim shares the start fence: cancellation of
      // the parent/attempt cannot slip between authorization and the child CAS.
      await tx.exec('UPDATE flow_runs SET flow_id = flow_id WHERE flow_id = ?', [parentFlowId]);
      await tx.exec('UPDATE flow_runs SET flow_id = flow_id WHERE flow_id = ?', [childFlowId]);
      for (let attempt = 0; attempt < maxCasAttempts; attempt++) {
        const row = await this.require(childFlowId, tx, true);
        const current = this.childRecord(row, parentFlowId);
        conflict(current.revision === input.expectedRevision, 'Child revision conflict');
        conflict(!terminal.has(row.status), 'Child is terminal');
        const identity = current.stateJson._fanoutIdentity as ObjectValue;
        this.validateChildState(incoming, identity);
        conflict(incoming._fanoutRevision === undefined || incoming._fanoutRevision === input.expectedRevision, 'Child state revision conflict');
        conflict(current.stateJson._fanoutStarted !== true || incoming._fanoutStarted !== false, 'Child started claim cannot be reversed');
        let deadline = this.deadlineGuard();
        if (current.stateJson._fanoutStarted === false && incoming._fanoutStarted === true) {
          const parent = await this.require(parentFlowId, tx, true);
          const group = storedGroup(parseState(parent.state_json), parentFlowId, String(identity.parentNodeId));
          const child = group?.children.find(c => c.childFlowId === childFlowId);
          conflict(parentActive.has(parent.status) && group?.status === 'running'
            && group.groupExecutionId === identity.groupExecutionId && child
            && !Object.hasOwn(child, 'dispatchClaimToken')
            && ['dispatch_pending', 'dispatch_unknown', 'running'].includes(child.status), 'Child execution is no longer authorized');
          deadline = this.deadlineGuard(group.deadlineAt);
        }
        if (row.status === 'cancelling' || row.status === 'cancel_requested') conflict(!parentActive.has(input.status), 'Child cancellation cannot be reversed');
        const state = { ...mergeMirror(current.stateJson, incoming), _fanoutIdentity: identity,
          _fanoutRevision: current.revision + 1, _fanoutStarted: incoming._fanoutStarted ?? current.stateJson._fanoutStarted };
        const guard = this.stateGuard(row.state_json);
        const result = await tx.exec(`UPDATE flow_runs SET state_json = ?, status = ?, current_phase = COALESCE(?, current_phase),
          completed_at = ? WHERE flow_id = ? AND ${guard.sql} AND status = ?${deadline.sql}`,
        [JSON.stringify(state), input.status, input.currentStep ?? null, terminal.has(input.status) ? Math.floor(Date.now() / 1000) : null,
          childFlowId, ...guard.params, row.status, ...deadline.params]);
        if (result.affectedRows > 0) return { ...current, stateJson: state, revision: state._fanoutRevision, status: input.status };
      }
      throw new FanoutStorageError(409, 'Concurrent child checkpoint or expired deadline');
    });
  }
}
