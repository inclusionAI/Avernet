import { backendRequest, BackendRequestError, type BackendRequestOptions } from '@/services/backendApi/httpClient';
import type { BackendApiEnvelope } from '@/services/backendApi/types';
export class RoutineRequestError extends Error {
  constructor(message: string, public requestId?: string, public status?: number) {
    super(`${message}${requestId ? `（定位 ID：${requestId}）` : ''}`);
    this.name = 'RoutineRequestError';
  }
}
export async function sendRoutineRequest<T>(url: string, options: BackendRequestOptions): Promise<T> {
  try {
    const response = await backendRequest<BackendApiEnvelope<T>>(url, { ...options, retryOnTransient: false });
    const code = Number(response.code);
    const status = Math.floor(code / 1000);
    if (!Number.isFinite(code) || status < 200 || status >= 300 || response.data === null || response.data === undefined)
      throw new RoutineRequestError(
        `${response.message || '任务接口未返回有效结果'}${
          [502, 504].includes(status) ? '；结果可能尚未确认，请先刷新核实，不要重复提交' : ''
        }`,
        response.request_id,
        status,
      );
    return response.data;
  } catch (reason) {
    if (reason instanceof RoutineRequestError) throw reason;
    if (reason instanceof BackendRequestError) {
      const envelope = reason.data as BackendApiEnvelope<unknown> | undefined;
      const uncertain = [502, 504].includes(reason.status || 0);
      throw new RoutineRequestError(
        `${reason.message}${uncertain ? '；结果可能尚未确认，请先刷新核实，不要重复提交' : ''}`,
        envelope?.request_id,
        reason.status,
      );
    }
    throw reason;
  }
}
