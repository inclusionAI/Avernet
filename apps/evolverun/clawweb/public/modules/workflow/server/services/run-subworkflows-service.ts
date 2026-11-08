import { createHash } from 'node:crypto';
import type { RunViewScope } from '@avernet/clawweb-shared/server/services/run-view-permissions';
import { FanoutReadError, type AuthorizedRunListQuery, type AuthorizedWorkflowStats, type FanoutCounts, type FanoutPage, type FanoutQuery, type RunIdentity,
  type RunReader, type RunReadInspection, type RunSubviewPermissions, type RunSubworkflowsReader,
  type RunSubworkflowsStore, type FanoutOriginCandidate } from '../contracts/run-subworkflows.js';
import { readFanoutMap, readFanoutState, type FanoutStateView } from './fanout-state-reader.js';

export function canReadRun(scope: RunViewScope, run: RunIdentity): boolean {
  if (scope === 'all') return true;
  if (scope === 'deny' || !run.origin_bot_id) return false;
  return scope.bots.some(({ botId, ownerId }) => ownerId === '*'
    ? run.origin_bot_id!.startsWith(`${botId}:`) && run.origin_bot_id!.length > botId.length + 1
    : run.origin_bot_id === `${botId}:${ownerId}`);
}
const hash = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const requestHash = (key: string) => createHash('sha256').update(key, 'utf8').digest('hex');

// Ordinary associations do not encode node or group identity. Ensure every
// flat item's actual request hash is unique across the entire parent snapshot,
// including items that are not yet bound. Approved attempts snapshots retain
// their existing legacy behavior and are not ordinary request associations.
function readGroups(map: Record<string, unknown>, parentFlowId: string): FanoutStateView[] {
  const groups = Object.entries(map).map(([nodeId, value]) => readFanoutState(value, parentFlowId, nodeId));
  const seen = new Set<string>();
  for (const group of groups) for (const item of group.children) for (const attempt of item.attempts) {
    if (attempt.associationKey === null) continue;
    const key = requestHash(attempt.associationKey);
    if (seen.has(key)) throw new FanoutReadError('invalid_state', 'Ambiguous fanout association identity');
    seen.add(key);
  }
  return groups;
}
// Project only execution metadata, never opaque engine state or node payloads.
// Status and node/phase identifiers are labels, not a general string channel.
const safeLabel = (value: unknown): value is string => typeof value === 'string'
  && /^[\p{L}\p{N}_. -]{1,128}$/u.test(value) && !['__proto__', 'constructor', 'prototype'].includes(value);
const nodeStatuses = new Set(['pending', 'running', 'postActionsRunning', 'waiting', 'succeeded', 'failed', 'rejected', 'blocked', 'skipped']);
function projectChildState(state: Record<string, unknown>, workflowId: string): string {
  const safe: Record<string, unknown> = {};
  if (state.workflowId === workflowId) safe.workflowId = workflowId;
  if (Number.isSafeInteger(state.workflowVersion) && (state.workflowVersion as number) >= -1) safe.workflowVersion = state.workflowVersion;
  if (safeLabel(state.businessStatus)) safe.businessStatus = state.businessStatus;
  if (safeLabel(state.currentPhase)) safe.currentPhase = state.currentPhase;
  if (typeof state.executionMode === 'string' && ['private', 'dingtalk-group', 'bcs-group'].includes(state.executionMode)) safe.executionMode = state.executionMode;
  if (Array.isArray(state.activeNodes)) safe.activeNodes = state.activeNodes.filter(safeLabel);
  if (state.nodeStates && typeof state.nodeStates === 'object' && !Array.isArray(state.nodeStates)) {
    const nodes: Record<string, unknown> = Object.create(null);
    for (const [id, value] of Object.entries(state.nodeStates)) {
      if (!safeLabel(id) || !value || typeof value !== 'object' || Array.isArray(value)) continue;
      const node = value as Record<string, unknown>; const projected: Record<string, unknown> = {};
      if (typeof node.status === 'string' && nodeStatuses.has(node.status)) projected.status = node.status;
      for (const key of ['phase', 'executor'] as const) if (safeLabel(node[key])) projected[key] = node[key];
      for (const key of ['attempts', 'manualRetries', 'startedAt', 'updatedAt', 'completedAt'] as const) {
        if (typeof node[key] === 'number' && Number.isSafeInteger(node[key]) && (node[key] as number) >= 0) projected[key] = node[key];
      }
      nodes[id] = projected;
    }
    safe.nodeStates = nodes;
  }
  return JSON.stringify(safe);
}
const activeStatuses = new Set(['dispatch_pending', 'dispatch_unknown', 'running', 'waiting', 'cancel_requested']);

export class RunSubworkflowsService implements RunSubworkflowsReader {
  constructor(private readonly store: RunSubworkflowsStore, private readonly permissions: RunSubviewPermissions) {}

  async listRuns(actor: RunReader, query: AuthorizedRunListQuery) {
    this.scopeResolver(actor); // Enforce identity before resolving any visibility.
    if (!Number.isSafeInteger(query.limit) || query.limit < 1 || query.limit > 2000 || !Number.isSafeInteger(query.offset) || query.offset < 0) {
      throw new FanoutReadError('invalid_query', 'Invalid list pagination');
    }
    return this.store.listRuns(query, actor.isAdmin ? null : await this.permissions.listRunViewScopes(actor.actorId!, query.workflowId));
  }

  async workflowStats(actor: RunReader) {
    this.scopeResolver(actor);
    const scopes = actor.isAdmin ? null : await this.permissions.listRunViewScopes(actor.actorId!);
    // Admin keeps the established unfiltered aggregation; all other rows use owner-qualified predicates.
    if (scopes === null) return { stats: [], visibleWorkflowIds: null };
    const stats: AuthorizedWorkflowStats[] = [];
    const visibleWorkflowIds = [...scopes].filter(([, scope]) => scope !== 'deny').map(([id]) => id);
    for (const workflowId of visibleWorkflowIds) {
      const page = await this.store.listRuns({ workflowId, limit: 1, offset: 0 }, new Map([[workflowId, scopes.get(workflowId)!]]));
      const latest = page.runs[0];
      if (latest) stats.push({ workflow_id: workflowId, workflow_title: latest.workflow_title as string | null,
        run_count: page.total, last_status: String(latest.status), last_run_at: Number(latest.started_at) });
    }
    return { stats, visibleWorkflowIds };
  }

  private scopeResolver(actor: RunReader) {
    if (!actor.isAdmin && !actor.actorId) throw new FanoutReadError('unauthenticated', 'User identity required');
    const cache = new Map<string, Promise<RunViewScope>>();
    return (workflowId: string): Promise<RunViewScope> => {
      if (actor.isAdmin) return Promise.resolve('all');
      if (!cache.has(workflowId)) cache.set(workflowId, this.permissions.resolveRunViewScope(workflowId, actor.actorId!));
      return cache.get(workflowId)!;
    };
  }

  private async authorizedRun(actor: RunReader, flowId: string) {
    const scope = this.scopeResolver(actor);
    const run = await this.store.readRun(flowId);
    if (!run) throw new FanoutReadError('not_found', 'Run not found');
    const parentScope = await scope(run.workflow_id);
    if (!canReadRun(parentScope, run)) throw new FanoutReadError('forbidden', 'Run view permission required');
    return { run, scope, parentScope };
  }

  private async visibility(group: FanoutStateView, storedGroup: unknown, scope: (id: string) => Promise<RunViewScope>, parentScope: RunViewScope) {
    const current = group.children.map(item => ({ item, attempt: item.attempts.find(a => a.attemptNumber === item.activeAttemptNumber)! }));
    const childScope = await scope(group.childWorkflowId);
    const legacyIds = current.flatMap(({ attempt }) => attempt.associationKey === null && attempt.nativeFlowId !== null
      ? [attempt.nativeFlowId] : []);
    const legacy = legacyIds.length ? await this.store.readIdentities(legacyIds) : [];
    const identities = new Map(legacy.map(row => [row.flow_id, row]));
    // The reader already validated the shape. Preserve a digest on the parent
    // if one exists, but never infer one from inputs or a different digest field.
    const storedChildren = new Map((storedGroup as { children: Record<string, unknown>[] }).children
      .map(value => [value.itemId, value]));
    const candidates: FanoutOriginCandidate[] = current.flatMap(({ item, attempt }) => {
      if (attempt.associationKey === null || attempt.nativeFlowId === null) return [];
      const stored = storedChildren.get(item.itemId)!;
      return [{ nativeFlowId: attempt.nativeFlowId, parentFlowId: group.parentFlowId,
        parentNodeId: group.groupNodeId, groupExecutionId: group.groupExecutionId, itemId: item.itemId,
        itemIndex: item.itemIndex, attemptNumber: attempt.attemptNumber, childFlowId: attempt.associationKey,
        workflowId: group.childWorkflowId, ...(Object.hasOwn(stored, 'requestDigest') ? { requestDigest: stored.requestDigest } : {}) }];
    });
    const matched = candidates.length ? await this.store.readFanoutOriginIdentities(candidates) : [];
    const origins = new Map(matched.map(row => [row.flow_id, row]));
    const visible = current.flatMap(({ item, attempt }) => {
      if (attempt.nativeFlowId === null) return [];
      const child = attempt.associationKey === null ? identities.get(attempt.nativeFlowId) : origins.get(attempt.nativeFlowId);
      return child && child.workflow_id === group.childWorkflowId && canReadRun(childScope, child)
        ? [{ item, attempt, child }] : [];
    });
    return { visible, full: parentScope === 'all' && childScope === 'all' && visible.length === current.length,
      authorization: [parentScope, childScope, visible.map(({ child }) => child)] };
  }

  async isFanoutOriginChild(flowId: string): Promise<boolean | null> {
    const run = await this.store.readRun(flowId);
    if (!run) return null;
    if (run.state_json === null || run.state_json === '') return false;
    if (typeof run.state_json !== 'string') throw new Error('Run state unavailable');
    const state: unknown = JSON.parse(run.state_json);
    return state !== null && typeof state === 'object' && Object.hasOwn(state, 'fanoutOrigin');
  }

  async inspectRun(actor: RunReader, flowId: string): Promise<RunReadInspection> {
    const { run, scope, parentScope } = await this.authorizedRun(actor, flowId);
    const map = readFanoutMap(run);
    const groups = readGroups(map, flowId);
    let restricted = false;
    for (const group of groups) if (!(await this.visibility(group, map[group.groupNodeId], scope, parentScope)).full) restricted = true;
    // The incarnation marker is internal proof, not part of run detail APIs.
    const { ordinary_run_marker: _marker, ...publicRun } = run;
    // A child can be readable without its parent; expose only safe execution metadata.
    const state = run.state_json ? JSON.parse(run.state_json) as Record<string, unknown> : null;
    const sensitiveChild = !!state && Object.hasOwn(state, 'fanoutOrigin');
    const sessionConfigured = sensitiveChild ? !!run.origin_bot_id && !!run.origin_session_key : undefined;
    if (sensitiveChild) restricted = true;
    if (sensitiveChild && state) {
      publicRun.state_json = projectChildState(state, run.workflow_id);
      publicRun.params_json = null; publicRun.input_json = null; publicRun.result_json = null;
      publicRun.origin_session_key = null; publicRun.origin_session_id = null;
    }
    return { run: publicRun, managed: groups.length > 0, restricted, sensitiveChild, sessionConfigured,
      groups: groups.map(group => ({ nodeId: group.groupNodeId, groupExecutionId: group.groupExecutionId })) };
  }

  async readGroup(actor: RunReader, flowId: string, nodeId: string, query: FanoutQuery): Promise<FanoutPage> {
    const limit = query.limit ?? 30;
    if (!query.groupExecutionId?.trim() || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
      throw new FanoutReadError('invalid_query', 'groupExecutionId and limit (1–100) are required');
    }
    const { run, scope, parentScope } = await this.authorizedRun(actor, flowId);
    const map = readFanoutMap(run);
    if (!Object.hasOwn(map, nodeId)) throw new FanoutReadError('not_found', 'No persisted fanout for this node');
    const group = readGroups(map, flowId).find(value => value.groupNodeId === nodeId)!;
    if (group.groupExecutionId !== query.groupExecutionId) throw new FanoutReadError('identity_conflict', 'Group execution changed; refresh the parent run');
    const { visible, full, authorization } = await this.visibility(group, map[group.groupNodeId], scope, parentScope);
    const snapshotId = hash([actor, group.groupExecutionId, group.revision, group.parentFlowId, group.groupNodeId, authorization, visible]);
    let offset = 0;
    if (query.cursor !== undefined) {
      let cursor: { snapshotId: string; offset: number };
      try {
        if (!/^[\w-]{1,2048}$/.test(query.cursor)) throw new Error();
        cursor = JSON.parse(Buffer.from(query.cursor, 'base64url').toString('utf8'));
        if (!cursor || typeof cursor.snapshotId !== 'string' || !Number.isSafeInteger(cursor.offset) || cursor.offset < 0) throw new Error();
      } catch { throw new FanoutReadError('invalid_query', 'Invalid cursor'); }
      if (cursor.snapshotId !== snapshotId) throw new FanoutReadError('snapshot_changed', 'Snapshot or permissions changed; refresh the first page');
      offset = cursor.offset;
      if (offset > visible.length) throw new FanoutReadError('invalid_query', 'Cursor is outside the visible snapshot');
    }
    const counts: FanoutCounts = { successCount: 0, failedCount: 0, pendingCount: 0, cancelledCount: 0, timedOutCount: 0, rejectedCount: 0 };
    const countKey: Record<string, keyof FanoutCounts> = { succeeded: 'successCount', failed: 'failedCount', cancelled: 'cancelledCount',
      timed_out: 'timedOutCount', rejected: 'rejectedCount' };
    for (const { attempt } of visible) counts[countKey[attempt.status] ?? 'pendingCount']++;
    const children = visible.slice(offset, offset + limit).map(({ item, attempt }, index) => ({
      displayIndex: offset + index, ...(full ? { itemIndex: item.itemIndex } : {}), itemId: item.itemId,
      attemptNumber: attempt.attemptNumber, childFlowId: attempt.nativeFlowId!, status: attempt.status, error: attempt.error,
    }));
    const nextOffset = offset + children.length;
    return { schemaVersion: 'fanout.v1', parentFlowId: flowId, groupNodeId: nodeId, groupExecutionId: group.groupExecutionId,
      revision: group.revision, snapshotId, status: group.status, childWorkflowId: group.childWorkflowId,
      concurrency: group.concurrency, successPolicy: group.successPolicy, countScope: full ? 'full' : 'visible',
      groupTotal: full ? visible.length : null, visibleTotal: visible.length, returnedCount: children.length,
      activeCount: visible.filter(({ attempt }) => activeStatuses.has(attempt.status)).length, counts, children,
      nextCursor: nextOffset < visible.length ? Buffer.from(JSON.stringify({ snapshotId, offset: nextOffset })).toString('base64url') : null };
  }
}
