import type { BotCreateInput } from '@/services/botWorkshop';
import { botAvatarService } from '@/services/botWorkshop/botAvatarService';
import { toBotCreateRequest } from '@/services/botWorkshop/botCreatePolicy';
import { resolveAfterCreateActions, runAfterCreateActions } from '@/services/botWorkshop/agentCodingAfterCreateService';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import {
  createExpertBot,
  recoverExpertBot,
  getExpertBotApplyReport,
  getExpertBotLastApply,
  classifyExpertSubmissionFailure,
  getExpertBotCreationStatus,
  type ExpertBotAccepted,
  type ExpertBotCreationStatus,
} from '@/services/expertMarketService';
import { useEffect, useRef, useState } from 'react';

const TERMINAL = new Set(['READY', 'CREATE_FAILED', 'APPLY_FAILED', 'AUTHORIZATION_REJECTED', 'AUTHORIZATION_EXPIRED']);

/** 专家创建只提交一次；初始化读原任务，恢复读新 Apply 报告。 */
export function useExpertBotCreation(open: boolean, existingBotId?: string) {
  const [accepted, setAccepted] = useState<ExpertBotAccepted>();
  const [status, setStatus] = useState<ExpertBotCreationStatus>();
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const [initializing, setInitializing] = useState(false);
  const [initializationPending, setInitializationPending] = useState(false);
  const [afterCreateError, setAfterCreateError] = useState<string>();
  const [recoveryId, setRecoveryId] = useState<string>();
  const [recoveryLookupAfterId, setRecoveryLookupAfterId] = useState<string>();
  const [recovering, setRecovering] = useState(false);
  const recoverySubmitting = useRef(false);
  const generation = useRef(0);
  const locked = useRef(false);
  const avatar = useRef<string>();
  const submitted = useRef<BotCreateInput>();
  const afterCreateStartedFor = useRef<string>();
  useEffect(() => () => { generation.current += 1; }, []);
  useEffect(() => {
    if (open) return;
    generation.current += 1;
    setRecoveryId(undefined);
    setRecoveryLookupAfterId(undefined);
    setRecovering(false);
    recoverySubmitting.current = false;
    setAccepted(undefined);
    setStatus(undefined);
    setError(undefined);
    setUnknown(false);
    setSubmitting(false);
    setInitializing(false);
    setInitializationPending(false);
    setAfterCreateError(undefined);
    submitted.current = undefined;
    afterCreateStartedFor.current = undefined;
    locked.current = false;
  }, [open]);
  useEffect(() => {
    if (open && existingBotId) setAccepted({ bot_id: existingBotId, iframe_url: '', redirect_url: '' });
  }, [open, existingBotId]);
  useEffect(() => {
    if (!open || !accepted?.bot_id) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await getExpertBotCreationStatus(accepted.bot_id);
        if (!active) return;
        setStatus(next);
        setError(undefined);
        if (TERMINAL.has(next.state)) {
          const submittedAvatar = avatar.current;
          if (next.bot && ['READY', 'APPLY_FAILED'].includes(next.state) && afterCreateStartedFor.current !== next.bot_id) {
            afterCreateStartedFor.current = next.bot_id;
            const agentCoding = submitted.current?.agentCoding;
            if (agentCoding?.template) {
              setInitializing(true);
              try {
                const failures = await runAfterCreateActions({
                  botId: next.bot_id,
                  ownerId: mapBotDto(next.bot, next.bot_id).item.ownerId,
                  template: agentCoding.template as AgentCodingTemplate,
                  values: agentCoding.values,
                });
                if (active && failures.length) setAfterCreateError(failures.map((failure) => failure.error.message).join('；'));
              } catch (failure) {
                if (active) setAfterCreateError(failure instanceof Error ? failure.message : '模板初始化失败');
              } finally {
                if (active) setInitializing(false);
              }
            }
          }
          if (next.bot && submittedAvatar) {
            try {
              await botAvatarService.save(next.bot_id, submittedAvatar);
            } catch {
              if (active) setError('Bot 已创建，但头像保存失败，可在 Bot 管理中修改头像。');
            }
          }
          if (active) setInitializationPending(false);
          return;
        }
      } catch (failure) {
        if (!active) return;
        setError(failure instanceof Error ? failure.message : '状态查询失败，正在重试');
      }
      if (active) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [open, accepted?.bot_id]);
  useEffect(() => {
    if (!open || !accepted?.bot_id || (!recoveryId && recoveryLookupAfterId === undefined)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const report = recoveryId
          ? await getExpertBotApplyReport(accepted.bot_id, recoveryId)
          : await getExpertBotLastApply(accepted.bot_id);
        if (!active) return;
        if (!recoveryId) {
          // A lost POST response is not permission to repeat the write. Only
          // a new bound recovery report can give this attempt a polling handle.
          if (report.apply_id && report.apply_id !== recoveryLookupAfterId
              && report.trigger === 'explicit' && report.state?.kind === 'manifest' && report.state.reference) {
            setRecoveryId(report.apply_id);
            setRecoveryLookupAfterId(undefined);
            return;
          }
          timer = setTimeout(poll, 2000);
          return;
        }
        if (report.apply_id !== recoveryId) throw new Error('配置恢复报告尚不可用，正在重试');
        setError(undefined);
        setStatus((current) => current && ({
          ...current, apply: report, message: undefined,
          state: report.result === 'RUNNING' ? 'APPLYING' : report.result === 'SUCCEEDED' ? 'READY' : 'APPLY_FAILED',
        }));
        if (report.result !== 'RUNNING') {
          setRecovering(false);
          return;
        }
      } catch (failure) {
        if (!active) return;
        setError(failure instanceof Error ? failure.message : '配置恢复状态查询失败，正在重试');
      }
      if (active) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [open, accepted?.bot_id, recoveryId, recoveryLookupAfterId]);
  return {
    accepted,
    status,
    error,
    submitting,
    recovering,
    unknown,
    initializing,
    initializationPending,
    afterCreateError,
    locked: submitting || Boolean(accepted) || unknown,
    async recover() {
      if (!accepted?.bot_id || status?.state !== 'APPLY_FAILED' || status.recoverable === false || recoverySubmitting.current || recovering) return;
      recoverySubmitting.current = true;
      setRecovering(true);
      setError(undefined);
      const version = generation.current;
      let posted = false;
      let previousApplyId = '';
      try {
        const previous = await getExpertBotLastApply(accepted.bot_id);
        if (version !== generation.current) return;
        previousApplyId = previous.apply_id || '';
        posted = true;
        const next = await recoverExpertBot(accepted.bot_id);
        if (version !== generation.current) return;
        setStatus((current) => current && ({ ...current, state: 'APPLYING', message: undefined }));
        setRecoveryId(next.apply_id);
      } catch (failure) {
        if (version !== generation.current) return;
        if (posted && classifyExpertSubmissionFailure(failure) === 'unknown') {
          setRecoveryId(undefined);
          setRecoveryLookupAfterId(previousApplyId);
          setError('配置恢复请求结果未知，正在查询原 Bot 的最新报告，请勿重复提交。');
        } else {
          setRecovering(false);
          setError(failure instanceof Error ? failure.message : '配置恢复未能确认，请到 Bot 管理查看');
        }
      } finally {
        if (version === generation.current) recoverySubmitting.current = false;
      }
    },
    async submit(expertId: string, input: BotCreateInput) {
      if (locked.current) return;
      const body = toBotCreateRequest(input);
      const template = input.agentCoding?.template as AgentCodingTemplate | undefined;
      const needsInitialization = Boolean(template && resolveAfterCreateActions(template).length);
      submitted.current = input;
      avatar.current = input.avatarUrl;
      locked.current = true;
      setSubmitting(true);
      setError(undefined);
      try {
        const result = await createExpertBot(expertId, body);
        if (!result.bot_id) throw new Error('创建响应缺少任务编号');
        setInitializationPending(needsInitialization);
        setAccepted(result);
      } catch (failure) {
        // 明确的请求校验拒绝允许用户修正；连接中断/5xx/无句柄不能推断未创建。
        const rejected = classifyExpertSubmissionFailure(failure) === 'rejected';
        setUnknown(!rejected);
        locked.current = !rejected;
        setError(failure instanceof Error ? failure.message : '创建结果未知');
      } finally {
        setSubmitting(false);
      }
    },
  };
}
