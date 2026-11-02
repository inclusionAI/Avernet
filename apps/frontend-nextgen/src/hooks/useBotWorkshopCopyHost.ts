import { canCopyBot, type BotDomain } from '@/domain/botWorkshop';
import { useCallback, useState } from 'react';
import { useBotWorkshopCopyFlow } from './useBotWorkshopCopyFlow';

interface UseBotWorkshopCopyHostOptions {
  /** 复制 / 授权走通后刷新列表,让新 Bot 落入当前页。 */
  load: () => Promise<void>;
  /** canCopyBot 的 engine + 编辑权限判定依赖当前用户 ID。 */
  currentOpenApiUserId?: string;
}

/**
 * 复制 Bot 特性开关。在建阶段,默认关闭:菜单不渲染「复制 Bot」项、openCopy no-op。
 * 放开时改为 true 即整体启用;不查后端,纯前端常量。
 */
const BOT_COPY_FEATURE_ENABLED = false;

/**
 * 编排「复制 Bot」:源 Bot 引用、Modal 开合、AgentPass 授权与列表刷新,
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
  return {
    copyBot,
    openCopy,
    closeCopy,
    copying: copyFlow.copying,
    copyAuthorization: copyFlow.authorization,
    submitCopy: copyFlow.submitCopy,
    getCopyAvailability,
  };
}

