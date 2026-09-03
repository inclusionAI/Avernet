import type { BotCreateAuthorization, BotDomain } from '@/services/botWorkshop';
import { botWorkshopService } from '@/services/botWorkshop';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export type BotCreateAuthorizationView = BotCreateAuthorization & { message?: string; error?: string };

export function useBotCreateAuthorization(onCreated: (bot: BotDomain) => Promise<void>) {
  const [authorization, setAuthorization] = useState<BotCreateAuthorizationView>();

  useEffect(() => {
    if (!authorization) return;
    let active = true;
    let polling = false;
    let terminal = false;
    const poll = async () => {
      if (polling || terminal) return;
      polling = true;
      try {
        const result = await botWorkshopService.pollCreateAuthorization(
          authorization.botId,
          authorization.request,
          authorization.agentCoding,
        );
        if (!active) return;
        if (result.status === 'ISSUED' && result.bot) {
          setAuthorization(undefined);
          if (result.afterCreateFailures?.length) {
            const actions = result.afterCreateFailures.map((failure) => failure.key).join('、');
            console.warn('[BotCreateAuthorization] after-create actions failed:', result.afterCreateFailures);
            toast.warning(`Bot 已创建，但后续配置未全部完成：${actions}`);
          }
          await onCreated(result.bot);
          return;
        }
        if (result.status === 'PENDING') {
          setAuthorization((current) =>
            current ? { ...current, message: result.message, error: undefined } : current,
          );
          return;
        }
        terminal = true;
        setAuthorization((current) =>
          current ? { ...current, error: `授权未完成（${result.status}），请关闭后重新创建` } : current,
        );
      } catch (error) {
        if (active) {
          setAuthorization((current) =>
            current
              ? { ...current, error: error instanceof Error ? error.message : '授权状态确认失败，请稍后重试' }
              : current,
          );
        }
      } finally {
        polling = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [authorization?.botId, authorization?.request, onCreated]);

  return {
    authorization,
    beginAuthorization: useCallback((next: BotCreateAuthorization) => setAuthorization(next), []),
    cancelAuthorization: useCallback(() => setAuthorization(undefined), []),
  };
}
