import { createHash } from 'node:crypto';
import type { RunViewScope } from '@avernet/clawweb-shared/server/services/run-view-permissions';
import { FanoutReadError, type AuthorizedRunListQuery, type AuthorizedWorkflowStats, type FanoutCounts, type FanoutPage, type FanoutQuery, type RunIdentity,
  type RunReader, type RunReadInspection, type RunSubviewPermissions, type RunSubworkflowsReader,
  type RunSubworkflowsStore } from '../contracts/run-subworkflows.js';
import { readFanoutMap, readFanoutState, type FanoutStateView } from './fanout-state-reader.js';

export function canReadRun(scope: RunViewScope, run: RunIdentity): boolean {
  if (scope === 'all') return true;
  if (scope === 'deny' || !run.origin_bot_id) return false;
  return scope.bots.some(({ botId, ownerId }) => ownerId === '*'
    ? run.origin_bot_id!.startsWith(`${botId}:`) && run.origin_bot_id!.length > botId.length + 1
    : run.origin_bot_id === `${botId}:${ownerId}`);
}
const hash = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
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

  private async visibility(group: FanoutStateView, scope: (id: string) => Promise<RunViewScope>, parentScope: RunViewScope) {
    const current = group.children.map(item => ({ item, attempt: item.attempts.find(a => a.attemptNumber === item.activeAttemptNumber)! }));
    const identities = new Map((await this.store.readIdentities(current.map(({ attempt }) => attempt.childFlowId))).map(row => [row.flow_id, row]));
    const childScope = await scope(group.childWorkflowId);
    const visible = current.filter(({ attempt }) => {
      const child = identities.get(attempt.childFlowId);
      // Missing/unproven origins and foreign workflows never inherit parent access.
      return child && child.workflow_id === group.childWorkflowId && canReadRun(childScope, child);
    });
    return { visible, full: parentScope === 'all' && childScope === 'all' && visible.length === current.length,
      authorization: [parentScope, childScope, visible.map(({ attempt }) => identities.get(attempt.childFlowId))] };
  }

  async inspectRun(actor: RunReader, flowId: string): Promise<RunReadInspection> {
    const { run, scope, parentScope } = await this.authorizedRun(actor, flowId);
    const groups = Object.entries(readFanoutMap(run)).map(([nodeId, value]) => readFanoutState(value, flowId, nodeId));
    let restricted = false;
    for (const group of groups) if (!(await this.visibility(group, scope, parentScope)).full) restricted = true;
    return { run, managed: groups.length > 0, restricted,
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
    const group = readFanoutState(map[nodeId], flowId, nodeId);
    if (group.groupExecutionId !== query.groupExecutionId) throw new FanoutReadError('identity_conflict', 'Group execution changed; refresh the parent run');
    const { visible, full, authorization } = await this.visibility(group, scope, parentScope);
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
      attemptNumber: attempt.attemptNumber, childFlowId: attempt.childFlowId, status: attempt.status, error: attempt.error,
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
