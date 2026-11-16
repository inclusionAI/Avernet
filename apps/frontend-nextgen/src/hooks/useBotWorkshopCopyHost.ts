import { canCopyBot, type BotDomain } from '@/domain/botWorkshop';
import { botWorkshopService } from '@/services/botWorkshop';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';
import { useBotWorkshopCopyFlow } from './useBotWorkshopCopyFlow';

interface UseBotWorkshopCopyHostOptions {
  /** 复制 / 授权走通后刷新列表,让新 Bot 落入当前页 / 跟随 copy_state。 */
  load: () => Promise<void>;
  /** canCopyBot 的 engine + 编辑权限判定依赖当前用户 ID。 */
  currentOpenApiUserId?: string;
}

/**
 * 复制 Bot 特性开关。在建阶段,默认关闭:菜单不渲染「复制 Bot」项、openCopy no-op、
 * BotStatusCell 的 copy_state 徽标 + 重试入口不渲染。放开时改为 true 即整体启用;
 * 不查后端,纯前端常量。export 出来供 BotStatusCell 同源消费,保证开关一处生效。
 */
export const BOT_COPY_FEATURE_ENABLED = false;

/**
 * 编排「复制 Bot」:源 Bot 引用、Modal 开合、AgentPass 授权面板(不轮询)与列表刷新,
 * 在 useBotWorkshop 之外承载,避免列表主 Hook 超过行数门禁(TC-G005 = 250 行)。
 * 结果由 useBotWorkshop 直接 spread 进返回对象,page 消费同一组字段。
 */
export function useBotWorkshopCopyHost({ load, currentOpenApiUserId }: UseBotWorkshopCopyHostOptions) {
  const [copyBot, setCopyBot] = useState<BotDomain>();
  const copyFlow = useBotWorkshopCopyFlow({ load, onClose: () => setCopyBot(undefined) });
  const openCopy = useCallback(
    (bot: BotDomain) => {
      if (!BOT_COPY_FEATURE_ENABLED) return;
      setCopyBot(bot);
    },
    [],
  );
  const closeCopy = useCallback(() => {
    copyFlow.cancelAuthorization();
    setCopyBot(undefined);
  }, [copyFlow.cancelAuthorization]);
  const getCopyAvailability = useCallback(
    (bot: BotDomain) => {
      if (!BOT_COPY_FEATURE_ENABLED) return { visible: false, enabled: false };
      return canCopyBot(bot, currentOpenApiUserId);
    },
    [currentOpenApiUserId],
  );
  /**
   * 重试复制(dst bot_id):toast 文案按 result 收敛,success 后刷新列表跟随 copy_state。
   * 用例:tsll 仍 RUNNING 60s 内 → 提示稍候;CONVERGED → 无需重试;其余 → 重新入队。
   */
  const retryCopy = useCallback(
    async (botId: string) => {
      try {
        const { result } = await botWorkshopService.retryCopyBot(botId);
        if (result === 're_enqueued') {
          toast.success('已重新提交,稍候自动刷新');
          await load();
        } else if (result === 'still_running') {
          toast.warning('上一次重试还在 60s 内,请稍后再试');
        } else {
          toast.info('已复制完成,无需重试');
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '重试失败,请稍后再试');
      }
    },
    [load],
  );
  return {
    copyBot,
    openCopy,
    closeCopy,
    copying: copyFlow.copying,
    awaitingDetail: copyFlow.awaitingDetail,
    copyAuthorization: copyFlow.authorization,
    cancelCopyAuthorization: copyFlow.cancelAuthorization,
    submitCopy: copyFlow.submitCopy,
    retryCopy,
    getCopyAvailability,
  };
}
