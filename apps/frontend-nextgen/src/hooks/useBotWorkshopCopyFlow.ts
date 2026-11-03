import type { BotCopyInput, BotDomain } from '@/services/botWorkshop';
import { botWorkshopService } from '@/services/botWorkshop';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';
import { useBotCreateAuthorization } from './useBotCreateAuthorization';

interface BotWorkshopCopyFlowOptions {
  /** 复制成功 / 授权走通后,刷新列表以让新 Bot 出现。 */
  load: () => Promise<void>;
  /** 复制流程结束时(成功或终止)关闭源 Bot 引用。 */
  onClose: () => void;
}

/**
 * 编排 Bot 复制:调 `copyBot` →
 * - `issued`(success):复制已提交,dst bot 由 create_job 异步创建。toast + 刷新列表 +
 *   关 Modal,新 Bot 落入当前页并携带 copy_state 进度。
 * - `authorization_required`(auth_required):**与创建 iframe url 完全同款处理** ——
 *   复用 `useBotCreateAuthorization` 的 2s 轮询 dst bot 的 auth-status,ISSUED 后收敛。
 *   dst bot 在 ac_bots 已注册(即便仍在复制中),auth-status 端点对 copy dst 同样适用。
 *
 * 源 Bot 的协作者/外部渠道/工具授权/密钥不继承。
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
        // issued:dst bot 异步创建中,无 BotDto;toast + 刷新列表 + 关 Modal。
        toast.success('复制已提交,正在创建新 Bot');
        onClose();
        await load();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Bot 复制失败');
        throw error;
      } finally {
        setCopying(false);
      }
    },
    [authorization.beginAuthorization, load, onClose],
  );

  return {
    copying,
    authorization: authorization.authorization,
    cancelAuthorization: authorization.cancelAuthorization,
    submitCopy,
  };
}
