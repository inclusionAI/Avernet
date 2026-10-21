import type { CreateFeedbackInput } from '@/domain/feedback/types';
import { TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export type FeedbackEnvelope<T> = BackendApiEnvelope<T>;

/**
 * 提交反馈（Avernet task 域 Feedback contract §8.1）。
 * 默认走公开面；本地直连 Engine 时随 TASK_READ_API_BASE 切换到 /api/v1。
 * 无幂等键，每次成功提交即一条新记录。
 */
export function createFeedback(input: CreateFeedbackInput, signal?: AbortSignal) {
  return backendRequest<FeedbackEnvelope<{ id: number }>>(`${TASK_READ_API_BASE}/feedback`, {
    method: 'POST',
    data: { reporter_id: input.reporterId, module: input.module, content: input.content },
    signal,
  });
}
