import type { BotCreateInput } from '@/services/botWorkshop';
import { botAvatarService } from '@/services/botWorkshop/botAvatarService';
import { toBotCreateRequest } from '@/services/botWorkshop/botCreatePolicy';
import { runAfterCreateActions } from '@/services/botWorkshop/agentCodingAfterCreateService';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import {
  createExpertBot,
  classifyExpertSubmissionFailure,
  getExpertBotCreationStatus,
  type ExpertBotAccepted,
  type ExpertBotCreationStatus,
} from '@/services/expertMarketService';
import { useEffect, useRef, useState } from 'react';

const TERMINAL = new Set(['READY', 'CREATE_FAILED', 'APPLY_FAILED', 'AUTHORIZATION_REJECTED', 'AUTHORIZATION_EXPIRED']);

/** 专家创建只提交一次；202 后始终以原 Manifest 任务为事实源。 */
export function useExpertBotCreation(open: boolean) {
  const [accepted, setAccepted] = useState<ExpertBotAccepted>();
  const [status, setStatus] = useState<ExpertBotCreationStatus>();
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const [initializing, setInitializing] = useState(false);
  const [afterCreateError, setAfterCreateError] = useState<string>();
  const locked = useRef(false);
  const avatar = useRef<string>();
  const submitted = useRef<BotCreateInput>();
  const afterCreateStartedFor = useRef<string>();
  useEffect(() => {
    if (open) return;
    setAccepted(undefined);
    setStatus(undefined);
    setError(undefined);
    setUnknown(false);
    setSubmitting(false);
    setInitializing(false);
    setAfterCreateError(undefined);
    submitted.current = undefined;
    afterCreateStartedFor.current = undefined;
    locked.current = false;
  }, [open]);
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
  return {
    accepted,
    status,
    error,
    submitting,
    unknown,
    initializing,
    afterCreateError,
    locked: submitting || Boolean(accepted) || unknown,
    async submit(expertId: string, input: BotCreateInput) {
      if (locked.current) return;
      const body = toBotCreateRequest(input);
      submitted.current = input;
      avatar.current = input.avatarUrl;
      locked.current = true;
      setSubmitting(true);
      setError(undefined);
      try {
        const result = await createExpertBot(expertId, body);
        if (!result.bot_id) throw new Error('创建响应缺少任务编号');
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
