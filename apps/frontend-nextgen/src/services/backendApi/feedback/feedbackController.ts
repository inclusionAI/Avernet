import type { CreateFeedbackInput } from '@/domain/feedback/types';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export type FeedbackEnvelope<T> = BackendApiEnvelope<T>;

/**
 * 提交反馈（Avernet BBS contract §5.2.1）。
 * 预发阶段统一走内部 /api Unified 面（与 openapi 公共面契约等同，仅前缀差异）：
 * POST /api/v1/feedback body `{reporter_id, module, content}`；无幂等键，每次调用即一条新记录。
 * dev 经 /api/v1/feedback 代理直连 agentclawengine-pre（与 BBS §2.x 内面同 app）。
 */
export function createFeedback(input: CreateFeedbackInput, signal?: AbortSignal) {
  return backendRequest<FeedbackEnvelope<{ id: number }>>('/api/v1/feedback', {
    method: 'POST',
    data: { reporter_id: input.reporterId, module: input.module, content: input.content },
    signal,
  });
}
