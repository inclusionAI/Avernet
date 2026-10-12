import type { CreateFeedbackInput } from '@/domain/feedback/types';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export type FeedbackEnvelope<T> = BackendApiEnvelope<T>;

/**
 * 提交反馈（Avernet task 域 Feedback contract §8.1）。
 * 当前部署走内部面：POST /api/v1/collaboration/tasks/feedback body `{reporter_id, module, content}`。
 * 无幂等键，每次成功提交即一条新记录。
 */
export function createFeedback(input: CreateFeedbackInput, signal?: AbortSignal) {
  return backendRequest<FeedbackEnvelope<{ id: number }>>('/api/v1/collaboration/tasks/feedback', {
    method: 'POST',
    data: { reporter_id: input.reporterId, module: input.module, content: input.content },
    signal,
  });
}
