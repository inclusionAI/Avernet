import type {
  CallbackCompensationCreate,
  CallbackCompensationRange,
  CallbackCompensationScope,
} from '@/domain/taskEscortParity/callbackCompensationTypes';
import { backendRequest } from '@/services/backendApi/httpClient';
import {
  parseCallbackCompensationBatch,
  parseCallbackCompensationCapabilities,
  parseCallbackCompensationPreview,
} from './callbackCompensationProtocol';

const path = (workflowId: string) =>
  `/openapi/v1/harnessflow/api/workflows/${encodeURIComponent(workflowId)}/callback-compensations`;
function request(workflowId: string, suffix: string, method: 'GET' | 'POST', data: unknown, signal?: AbortSignal) {
  return backendRequest<unknown>(`${path(workflowId)}${suffix}`, {
    method,
    signal,
    injectUserId: false,
    retryOnTransient: false,
    suppressErrorNotification: true,
    ...(method === 'POST'
      ? { data, headers: { 'Content-Type': 'application/json', 'X-Callback-Compensation-CSRF': '1' } }
      : {}),
  });
}
/** Session cookies/auth gates remain in backendRequest; a user_id is not authorization. */
export const callbackCompensationService = {
  async capabilities(workflowId: string, signal?: AbortSignal) {
    return parseCallbackCompensationCapabilities(
      await request(workflowId, '/capabilities', 'GET', undefined, signal),
      workflowId,
    );
  },
  async preview(scope: CallbackCompensationScope, input: CallbackCompensationRange, signal?: AbortSignal) {
    const data = {
      configId: input.configId,
      ...(input.from !== undefined ? { from: input.from } : {}),
      ...(input.to !== undefined ? { to: input.to } : {}),
      ...(input.flowIds ? { flowIds: [...input.flowIds] } : {}),
    };
    return parseCallbackCompensationPreview(
      await request(scope.workflowId, '/preview', 'POST', data, signal),
      scope,
      input.configId,
    );
  },
  async create(scope: CallbackCompensationScope, input: CallbackCompensationCreate, signal?: AbortSignal) {
    const data = {
      previewId: input.previewId,
      itemIds: [...input.itemIds],
      reason: input.reason,
      requestKey: input.requestKey,
    };
    return parseCallbackCompensationBatch(await request(scope.workflowId, '', 'POST', data, signal), scope);
  },
  async batch(scope: CallbackCompensationScope, batchId: string, signal?: AbortSignal) {
    return parseCallbackCompensationBatch(
      await request(scope.workflowId, `/${encodeURIComponent(batchId)}`, 'GET', undefined, signal),
      scope,
      batchId,
    );
  },
  async stop(scope: CallbackCompensationScope, batchId: string, signal?: AbortSignal) {
    return parseCallbackCompensationBatch(
      await request(scope.workflowId, `/${encodeURIComponent(batchId)}/stop`, 'POST', {}, signal),
      scope,
      batchId,
    );
  },
};
