import type { CreateFeedbackInput } from '@/domain/feedback/types';
import { createFeedback } from '@/services/backendApi/feedback/feedbackController';
import { isEnvelopeSuccess } from '@/services/backendApi/types';

/**
 * 反馈 Service（BBS 实验反馈通道 §5.2）。领域校验在前端兜底：reporter_id 必填、content 非空且 ≤4000 字。
 * 后端无幂等键：每次成功提交即新增一条；Service 仅做信封解码与错误归一，返回新记录 id。
 */
export class FeedbackService {
  async createFeedback(input: CreateFeedbackInput, signal?: AbortSignal): Promise<{ id: number }> {
    const reporterId = input.reporterId.trim();
    const content = input.content.trim();
    if (!reporterId) throw new Error('缺少反馈人身份');
    if (!content) throw new Error('请输入反馈内容');
    if (content.length > 4000) throw new Error('反馈内容不能超过 4000 字');
    const response = await createFeedback({ reporterId, module: input.module || 'bbs', content }, signal);
    if (!isEnvelopeSuccess(response) || response.data === undefined) {
      throw new Error(response.message || '反馈提交失败，请稍后重试');
    }
    return { id: response.data.id };
  }
}

export const feedbackService = new FeedbackService();
