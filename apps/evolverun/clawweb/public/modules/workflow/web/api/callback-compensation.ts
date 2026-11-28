import type {
  CallbackCompensationCreate,
  CallbackCompensationRange,
  CallbackCompensationScope,
} from '../models/callbackCompensationTypes';
import { fetchJson } from '@avernet/clawweb-shared/web/api/client';
import {
  parseCallbackCompensationBatch,
  parseCallbackCompensationCapabilities,
  parseCallbackCompensationPreview,
} from './callback-compensation-protocol';

const path = (workflowId: string) =>
  `/api/workflows/${encodeURIComponent(workflowId)}/callback-compensations`;
/** Native transport: cookies and shared auth headers follow fetchJson; display headers never grant access.
 * No retry, especially after an ambiguous POST. Strip raw gateway/error bodies before they reach UI state. */
async function request(workflowId: string, suffix: string, method: 'GET' | 'POST', data: unknown, signal?: AbortSignal) {
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(() => controller.abort(), 30_000);
  try {
    return await fetchJson<unknown>(`${path(workflowId)}${suffix}`, {
      method, signal: controller.signal, credentials: 'include',
      ...(method === 'POST' ? {
        body: JSON.stringify(data),
        headers: { 'Content-Type': 'application/json', 'X-Callback-Compensation-CSRF': '1' },
      } : {}),
    });
  } catch (failure) {
    const source = failure as { status?: unknown; body?: unknown } | null;
    let code: string | undefined;
    try {
      const body = typeof source?.body === 'string' ? JSON.parse(source.body) : null;
      if (typeof body?.code === 'string' && /^[A-Z_]{3,64}$/.test(body.code)) code = body.code;
    } catch { /* Non-JSON gateway responses are intentionally not exposed. */ }
    throw Object.assign(new Error('补偿请求未完成'), {
      ...(typeof source?.status === 'number' ? { status: source.status } : {}),
      data: code ? { code } : {},
    });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}
/** Authorization is exclusively enforced by the native callback-compensation adapter. */
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
