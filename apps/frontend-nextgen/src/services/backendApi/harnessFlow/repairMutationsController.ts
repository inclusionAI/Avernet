import { backendRequest } from '../httpClient';

export interface RepairPublishDto {
  revision: number;
  expectedStateVersion: number;
  requestId: string;
  targetId: string;
  acknowledgeUncoveredChecks: true;
}

const task = (id: string) => `/openapi/v1/harnessflow/api/workflow-repairs/${encodeURIComponent(id)}`;
function request(path: string, userId: string, options: { data: object } | { signal: AbortSignal }) {
  // The same-origin Gateway authenticates and signs the principal. The bound
  // user_id is the existing selector, not an authorization header.
  if (!userId.trim()) throw new Error('缺少明确的用户身份');
  return backendRequest<unknown>(path, {
    method: 'data' in options ? 'POST' : 'GET',
    injectUserId: false,
    params: { user_id: userId },
    retryOnTransient: false,
    suppressErrorNotification: true,
    ...options,
  });
}

export const repairMutationsController = {
  deploymentTargets: (taskId: string, userId: string, signal: AbortSignal) =>
    request(`${task(taskId)}/deployment-targets`, userId, { signal }),
  publish: (taskId: string, userId: string, body: RepairPublishDto) =>
    request(`${task(taskId)}/publish`, userId, { data: body }),
  reconcile: (taskId: string, deploymentId: string, userId: string) =>
    request(`${task(taskId)}/deployment/${encodeURIComponent(deploymentId)}/reconcile`, userId, { data: {} }),
};
