// useBotResourceJump — 会话资源管理面板 → Bot 完整资源目录的跳转入口状态与执行。
//
// 面板内只展示当前会话产生的文件，完整资源目录在 Bot 详情「资源」tab。本 Hook 承担三件事：
//   1) 露出判定：好友 Bot 用户只是使用者、不是管理者，不露出入口；
//   2) 可用判定：Bot 视图未返回空间归属时置灰并给出原因；
//   3) 跳转执行：跨空间时先切空间再跳转——Bot 管理列表按当前空间过滤，
//      不切会导致用户从详情返回列表时看不到该 Bot；切换后以可撤销提示告知。
//
// 空间状态读 spaceContextStore（数字 spaceId），Bot 视图的 spaceId 为字符串，比较前统一转数字。
import { switchSpaceContext, useSpaceContext } from '@/hooks/useSpaceContext';
import type { BotResourceJump } from '@/pages/Workspace/components/BotSessionFiles/BotSessionFilesPanel';
import type { BotRuntimeStage } from '@/domain/botWorkshop';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { history } from '@umijs/max';
import { useMemo } from 'react';
import { toast } from 'sonner';

/** 跳转提示的停留时长：留足阅读与点击「撤销」的时间，超时视为接受当前空间。 */
const SWITCH_NOTICE_DURATION_MS = 10000;

export function useBotResourceJump(
  bot: ChatBotView | null,
  stage: BotRuntimeStage | undefined,
): BotResourceJump | undefined {
  const currentSpaceId = useSpaceContext((space) => space.currentSpaceId);

  return useMemo<BotResourceJump | undefined>(() => {
    if (!bot || bot.isFriendBot) return undefined;

    const targetSpaceId = bot.spaceId ? Number(bot.spaceId) : undefined;
    const spaceResolved = targetSpaceId !== undefined && Number.isFinite(targetSpaceId);
    const available = spaceResolved && Boolean(bot.realBotId);

    return {
      available,
      disabledReason: available ? undefined : '无法确定 Bot 所属空间，请手动切换',
      onJump: () => {
        if (!available || !bot.realBotId) return;
        const params = new URLSearchParams({ type: 'view', id: bot.realBotId, tab: 'resource' });
        if (bot.ownerId) params.set('owner_id', bot.ownerId);
        if (stage) params.set('runtime_stage', stage);

        const prevSpaceId = currentSpaceId;
        const switching = targetSpaceId !== prevSpaceId;
        if (switching) switchSpaceContext(targetSpaceId as number);
        history.push(`/bot-workshop/detail?${params.toString()}`);
        if (!switching) return;

        toast(`已切换至「${bot.spaceName ?? '目标空间'}」`, {
          duration: SWITCH_NOTICE_DURATION_MS,
          action:
            prevSpaceId === undefined
              ? undefined
              : { label: '撤销', onClick: () => switchSpaceContext(prevSpaceId) },
        });
      },
    };
  }, [bot, currentSpaceId, stage]);
}