import type { BotCopyInput, BotDomain } from '@/services/botWorkshop';
import { botWorkshopService } from '@/services/botWorkshop';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';
import { useBotCreateAuthorization } from './useBotCreateAuthorization';

interface BotWorkshopCopyFlowOptions {
  /** 复制成功 / 授权走完后,刷新列表以让新 Bot 出现。 */
  load: () => Promise<void>;
  /** 复制流程结束时(成功或终止)关闭源 Bot 引用。 */
  onClose: () => void;
}

/**
 * 编排 Bot 复制:调 `copyBot` → 需 AgentPass 授权时 `beginAuthorization`,
 * 复用 `useBotCreateAuthorization` 的 2s 轮询直到 ISSUED;
 * 成功后 toast + 刷新列表 + 关 Modal。结果结构与创建同构,授权 iframe 也共用。
 */
export function useBotWorkshopCopyFlow({ load, onClose }: BotWorkshopCopyFlowOptions) {
  const [copying, setCopying] = useState(false);
  const handleCopied = useCallback(
    async (bot: BotDomain) => {
      onClose();
      toast.success(`${bot.name} 已复制`);
      await load();
    },
    [load, onClose],
  );
  const handleAuthorizationTerminated = useCallback(
    (status: string, message?: string) => {
      onClose();
      toast.error(message || `AgentPass 授权未完成(${status})`);
    },
    [onClose],
  );
  const authorization = useBotCreateAuthorization(handleCopied, handleAuthorizationTerminated);
  const submitCopy = useCallback(
    async (input: BotCopyInput) => {
      setCopying(true);
      try {
        const result = await botWorkshopService.copyBot(input);
        if (result.type === 'authorization_required') {
          authorization.beginAuthorization(result);
          return;
        }
        if (result.type === 'created_with_pending_after_create') {
          const actions = result.afterCreateFailures.map((failure) => failure.key).join('、');
          toast.warning(`Bot 已复制,但后续配置未全部完成${actions ? `:${actions}` : ''}`);
        }
        await handleCopied(result.bot);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Bot 复制失败');
        throw error;
      } finally {
        setCopying(false);
      }
    },
    [authorization.beginAuthorization, handleCopied],
  );

  return {
    copying,
    authorization: authorization.authorization,
    cancelAuthorization: authorization.cancelAuthorization,
    submitCopy,
  };
}
