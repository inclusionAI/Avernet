import type { BotDomain } from '@/services/botWorkshop';
import { botHealthCheckService } from '@/services/botHealthCheck';
import { history } from '@umijs/max';
import { useCallback } from 'react';
import { toast } from 'sonner';

/**
 * BotWorkshop 列表的「健康检查」入口:把 Bot + 当前用户身份转换为跳转目标,
 * 缺身份时 toast 阻断;否则路由到 `/bot-workshop/health-check?id=<botId>`。
 * 从 useBotWorkshop 抽出,以避免列表主 Hook 超过 250 行门禁(TC-G005)。
 */
export function useBotWorkshopHealthCheck(currentOpenApiUserId?: string) {
  return useCallback(
    (bot: BotDomain) => {
      const target = botHealthCheckService.toTarget(bot, currentOpenApiUserId);
      if (!target) {
        const availability = botHealthCheckService.resolveAvailability(bot, currentOpenApiUserId);
        toast.error(availability.disabledReason ?? '缺少当前用户身份，无法发起健康检查');
        return;
      }
      history.push(`/bot-workshop/health-check?id=${encodeURIComponent(target.botId)}`);
    },
    [currentOpenApiUserId],
  );
}
