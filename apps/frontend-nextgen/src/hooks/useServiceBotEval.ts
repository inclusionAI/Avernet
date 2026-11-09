import {
  serviceBotEvalService,
  type ServiceBotEvalHistoryDto,
  type ServiceBotEvalSubmissionDto,
  type ServiceBotEvalTriggerSource,
} from '@/services/botWorkshop/serviceBotEvalService';
import { useCallback, useEffect, useState } from 'react';

const ACTIVE_TASK_STATUSES = new Set(['INIT', 'RUNNING', 'PENDING']);

export function useServiceBotEval(botId?: string, botVersion?: string, ownerId?: string) {
  const [detail, setDetail] = useState<Record<string, unknown>>({});
  const [history, setHistory] = useState<ServiceBotEvalHistoryDto | undefined>();
  const [submission, setSubmission] = useState<ServiceBotEvalSubmissionDto | undefined>();
  const [loading, setLoading] = useState(Boolean(botId && botVersion));
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string>();

  const load = useCallback(async () => {
    if (!botId || !botVersion) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [detailResult, historyResult] = await Promise.all([
        serviceBotEvalService.detail(botId, botVersion, ownerId),
        serviceBotEvalService.history(botId, botVersion, ownerId),
      ]);
      setDetail(detailResult as unknown as Record<string, unknown>);
      setHistory(historyResult);
      setActionError(undefined);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '评测详情加载失败');
    } finally {
      setLoading(false);
    }
  }, [botId, botVersion, ownerId]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = useCallback(
    async (sampleFiles: File[], reportFiles: File[], triggerSource: ServiceBotEvalTriggerSource) => {
      if (!botId || !botVersion) return;
      setSubmitting(true);
      try {
        const result = await serviceBotEvalService.submit(
          botId,
          botVersion,
          ownerId,
          sampleFiles,
          reportFiles,
          triggerSource,
        );
        setSubmission(result);
        setActionError(undefined);
        return result;
      } catch (error) {
        setActionError(error instanceof Error ? error.message : '提交评测数据失败');
        throw error;
      } finally {
        setSubmitting(false);
      }
    },
    [botId, botVersion, ownerId],
  );

  const refreshSubmission = useCallback(async (submissionId: string) => {
    if (!botId) return;
    try {
      const result = await serviceBotEvalService.submission(botId, submissionId, ownerId);
      setSubmission(result);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '评测状态刷新失败');
    }
  }, [botId, ownerId]);

  useEffect(() => {
    if (!submission?.submission_id || !ACTIVE_TASK_STATUSES.has(submission.task_status)) return;
    const timer = window.setTimeout(
      () => void refreshSubmission(submission.submission_id),
      3000,
    );
    return () => window.clearTimeout(timer);
  }, [refreshSubmission, submission]);

  const sync = useCallback(async () => {
    if (!botId || !botVersion) return;
    try {
      await serviceBotEvalService.sync(botId, botVersion, ownerId);
      await load();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '认证状态同步失败');
    }
  }, [botId, botVersion, ownerId, load]);

  return {
    detail,
    history,
    submission,
    loading,
    submitting,
    actionError,
    reload: load,
    submit,
    sync,
  };
}
