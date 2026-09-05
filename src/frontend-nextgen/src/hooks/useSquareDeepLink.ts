import { parseSquareDeepLink } from '@/domain/collaborationSquare/mapper';
import { notifyError } from '@/components/ui/notify';
import type {
  BotCatalogViewer,
  HumanBotActionContext,
  PublicBot,
  PublicGroup,
  SquareResource,
} from '@/domain/collaborationSquare/types';
import { collaborationSquareBotService } from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { getCollaborationSquareErrorMessage } from '@/utils/collaborationSquare';
import { useEffect, useRef } from 'react';

interface UseSquareDeepLinkOptions {
  resource: SquareResource;
  humanBotContext: HumanBotActionContext | null;
  viewer?: BotCatalogViewer;
  openSharedBot: (bot: PublicBot) => void;
  openGroupMembers: (group: PublicGroup) => Promise<void>;
  handleTargetInvalid: (resource: SquareResource, id: string) => void;
}

/**
 * bot/group 的 SquareDeepLink 深链解析。任务广场本期不接深链（Non-goal），resource='task' 时跳过。
 * Bot 未出现在当前页时，以公开名称调用 Catalog Search，再以 URL 中的 canonical ID 精确匹配。
 */
export function useSquareDeepLink({
  resource,
  humanBotContext,
  viewer,
  openSharedBot,
  openGroupMembers,
  handleTargetInvalid,
}: UseSquareDeepLinkOptions) {
  const loading = useCollaborationSquareStore((state) => state.loading);
  const bots = useCollaborationSquareStore((state) => state.bots);
  const groups = useCollaborationSquareStore((state) => state.groups);
  const handledTarget = useRef<string | null>(null);

  useEffect(() => {
    if (loading || resource === 'task' || typeof window === 'undefined') return;
    const deepLink = parseSquareDeepLink(window.location.search, resource);
    if (!deepLink) {
      handledTarget.current = null;
      return;
    }
    const targetKey = `${deepLink.resource}:${deepLink.id}:${deepLink.searchHint ?? ''}`;
    if (handledTarget.current === targetKey) return;

    if (resource === 'bot') {
      const loadedBot = bots.find((item) => item.id === deepLink.id);
      if (loadedBot) {
        handledTarget.current = targetKey;
        openSharedBot(loadedBot);
        return;
      }
      if (!deepLink.searchHint) {
        handledTarget.current = targetKey;
        handleTargetInvalid('bot', deepLink.id);
        return;
      }

      const controller = new AbortController();
      handledTarget.current = targetKey;
      void collaborationSquareBotService
        .resolveSharedBot(deepLink.id, deepLink.searchHint, humanBotContext ?? undefined, viewer, controller.signal)
        .then((bot) => {
          if (controller.signal.aborted) return;
          if (bot) openSharedBot(bot);
          else handleTargetInvalid('bot', deepLink.id);
        })
        .catch((error) => {
          if (!controller.signal.aborted && (error as Error).name !== 'AbortError')
            notifyError(getCollaborationSquareErrorMessage(error));
        });
      return () => controller.abort();
    }

    const group = groups.find((item) => item.id === deepLink.id);
    handledTarget.current = targetKey;
    if (group) void openGroupMembers(group);
    else handleTargetInvalid('group', deepLink.id);
  }, [
    bots,
    groups,
    handleTargetInvalid,
    humanBotContext,
    loading,
    openGroupMembers,
    openSharedBot,
    resource,
    viewer,
  ]);
}
