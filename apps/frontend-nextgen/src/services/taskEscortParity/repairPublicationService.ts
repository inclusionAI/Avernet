import {
  repairMutationsController as api,
  type RepairPublishDto,
} from '@/services/backendApi/harnessFlow/repairMutationsController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { match, object } from './evolutionMapper';

function nonempty(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error('发布响应协议不完整');
  return value;
}

const pendingStatuses = ['created', 'dispatching', 'running', 'uncertain'];
export const isPendingPublication = (status: string) => pendingStatuses.includes(status);

export interface RepairPublicationScope {
  userId: string;
  workflowId: string;
  taskId: string;
  revision: number;
  candidateCommit: string;
  packDigest: string;
}

export interface RepairWorkflowTarget {
  id: string;
  label: string;
  environment: string;
}

export type RepairPublishIntent = RepairPublishDto & { environment: string };

function validateDeployment(
  value: unknown,
  scope: RepairPublicationScope,
  identity: { targetId: string; environment: string; requestId: string },
) {
  const { targetId, environment, requestId } = identity;
  const row = object(value);
  match(row.workflowId, scope.workflowId);
  match(row.taskId, scope.taskId);
  match(row.revision, scope.revision);
  match(row.candidateCommit, scope.candidateCommit);
  match(row.packDigest, scope.packDigest);
  match(row.requestId, requestId);
  const target = object(row.target);
  match(target.scope, 'workflow');
  match(target.id, targetId);
  match(target.environment, environment);
  const status = nonempty(row.status);
  if (!isPendingPublication(status) && status !== 'failed' && status !== 'succeeded') {
    throw new Error('发布状态无法确认');
  }
  if (status === 'succeeded') {
    const receipt = object(row.receipt);
    match(receipt.candidateCommit, scope.candidateCommit);
    match(receipt.packDigest, scope.packDigest);
    match(receipt.targetId, targetId);
    match(receipt.requestId, requestId);
    if (
      typeof receipt.deployNumber !== 'number' ||
      !Number.isSafeInteger(receipt.deployNumber) ||
      receipt.deployNumber < 1
    ) {
      throw new Error('发布回执不完整');
    }
  }
  return nonempty(row.id);
}

// A lost response can leave only the approval persisted, with a bumped
// stateVersion and no deployment yet. Keep the entire intent across tab changes
// and reloads; never regenerate its key or substitute the new stateVersion.
function storageKey(scope: RepairPublicationScope) {
  return `task-escort.repair-publish:${JSON.stringify([
    scope.userId,
    scope.workflowId,
    scope.taskId,
    scope.revision,
    scope.candidateCommit,
    scope.packDigest,
  ])}`;
}

export const repairPublicationService = {
  async target(taskId: string, userId: string, signal: AbortSignal): Promise<RepairWorkflowTarget> {
    let response: unknown;
    try {
      response = await api.deploymentTargets(taskId, userId, signal);
    } catch (error) {
      const status = repairPublishStatus(error);
      throw new Error(status === 401 || status === 403 ? '当前账号无权读取发布范围' : '无法读取发布范围，请重试');
    }
    const { targets } = object(response);
    if (!Array.isArray(targets)) throw new Error('发布范围协议不完整');
    const scopes = targets.map(object).filter((target) => target.scope === 'workflow');
    if (!scopes.length) throw new Error('没有可发布的工作流范围');
    if (scopes.length !== 1) throw new Error('发布范围配置不明确');
    const { id, label, environment } = scopes[0];
    return { id: nonempty(id), label: nonempty(label), environment: nonempty(environment) };
  },
  pending(scope: RepairPublicationScope): RepairPublishIntent | null {
    const value = sessionStorage.getItem(storageKey(scope));
    if (value === null) return null;
    const row = object(JSON.parse(value));
    if (
      row.revision !== scope.revision ||
      typeof row.expectedStateVersion !== 'number' ||
      !Number.isSafeInteger(row.expectedStateVersion) ||
      row.expectedStateVersion < 0 ||
      row.acknowledgeUncoveredChecks !== true ||
      typeof row.requestId !== 'string' ||
      !/^[\da-f-]{36}$/.test(row.requestId)
    )
      throw new Error('原发布请求无法读取');
    return {
      revision: scope.revision,
      expectedStateVersion: row.expectedStateVersion,
      requestId: row.requestId,
      targetId: nonempty(row.targetId),
      environment: nonempty(row.environment),
      acknowledgeUncoveredChecks: true,
    };
  },
  reserve(scope: RepairPublicationScope, request: RepairPublishIntent) {
    sessionStorage.setItem(storageKey(scope), JSON.stringify(request));
  },
  clear(scope: RepairPublicationScope, requestId: string) {
    // Cleanup is best effort: an inaccessible entry can only replay the same intent.
    try {
      if (this.pending(scope)?.requestId === requestId) sessionStorage.removeItem(storageKey(scope));
    } catch {
      // Do not turn a confirmed server result into an uncertain one.
    }
  },
  async publish(scope: RepairPublicationScope, intent: RepairPublishIntent) {
    const { environment, ...body } = intent;
    // ClawWeb separates the approval's request ID from the deployment's ID.
    // Bind the returned deployment to SHA-256(canonical JSON ["publish", body.requestId]).
    const digest = crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(JSON.stringify(['publish', body.requestId])),
    );
    const [response, bytes] = await Promise.all([api.publish(scope.taskId, scope.userId, body), digest]);
    const requestId = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, '0')).join('');
    validateDeployment(response, scope, {
      targetId: body.targetId,
      environment,
      requestId,
    });
  },
  async reconcile(scope: RepairPublicationScope, deployment: Record<string, unknown>) {
    const id = nonempty(deployment.id);
    const target = object(deployment.target);
    match(target.scope, 'workflow');
    const identity = {
      targetId: nonempty(target.id),
      environment: nonempty(target.environment),
      requestId: nonempty(deployment.requestId),
    };
    match(validateDeployment(await api.reconcile(scope.taskId, id, scope.userId), scope, identity), id);
  },
};

export function repairPublishStatus(error: unknown): number | undefined {
  return error instanceof BackendRequestError ? error.status : undefined;
}
