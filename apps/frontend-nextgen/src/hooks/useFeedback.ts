import { notifyError, notifySuccess } from '@/components/ui/notify';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { feedbackService } from '@/services/feedback';
import { useCallback, useState } from 'react';

/**
 * 实验室「我要反馈」写能力：以当前登录人（reporter_id=工号）提交 BBS 实验反馈（module='bbs'）。
 * 仅负责写入 + 提示；不拉取反馈列表（产品端当前无列表视图）。
 */
export function useFeedback() {
  const { identity } = useHumanIdentity();
  const [submitting, setSubmitting] = useState(false);

  const submit = useCallback(
    async (content: string) => {
      const reporterId = identity?.userId.trim() ?? '';
      if (!reporterId) {
        notifyError('身份未就绪，请稍后重试', { title: '反馈失败' });
        return false;
      }
      setSubmitting(true);
      try {
        await feedbackService.createFeedback({ reporterId, module: 'bbs', content });
        notifySuccess('反馈已提交，感谢你的建议');
        return true;
      } catch (error) {
        notifyError(error instanceof Error && error.message ? error.message : '反馈提交失败', {
          title: '反馈失败',
        });
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [identity?.userId],
  );

  return { submit, submitting };
}
