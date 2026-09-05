import { notifyError, notifySuccess } from '@/components/ui/notify';
import type { IdentityView } from '@/domain/collaboration';
import {
  canStartPublicBotConversation,
  getPublicBotTargetId,
  type BotCatalogViewModel,
  type BotCatalogViewer,
  type HumanBotActionContext,
  type PublicBot,
  type PublicBotProfile,
} from '@/domain/collaborationSquare/types';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useBotCatalogFetch } from '@/pages/Workspace/hooks/useBotCatalogFetch';
import {
  CollaborationSquareError,
  collaborationSquareBotService,
  collaborationSquareService,
} from '@/services/collaborationSquare';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import {
  getCollaborationBotConversationUrl,
  getCollaborationSquareErrorMessage,
  getCollaborationSquareShareUrl,
} from '@/utils/collaborationSquare';
import { history } from '@umijs/max';
import { useCallback, useMemo, useState } from 'react';

interface UsePublicBotCatalogOptions {
  /** 当前角色 tab 身份；viewer 跟随它（human→userId，bot→bot id）。 */
  activeIdentity?: IdentityView | null;
  enabled: boolean;
}

/**
 * 添加好友弹窗（Bot 广场）数据层 Hook：组合 {@link useBotCatalogFetch} 读路径与写动作
 * （申请好友 / 立即开始对话 / 画像），以本地 state 产出 {@link BotCatalogViewModel}，
 * 与协作广场页共享同一展示组件。
 */
export function usePublicBotCatalog({ activeIdentity, enabled }: UsePublicBotCatalogOptions): BotCatalogViewModel {
  const { identity: humanIdentity } = useHumanIdentity();
  const actorId = useWorkspaceStore(
    (state) => state.identities.find((item) => item.kind === 'user' && !item.id.startsWith('test-'))?.id ?? null,
  );
  const humanBotContext = useMemo<HumanBotActionContext | null>(
    () => (actorId && humanIdentity?.userId ? { actorId, userId: humanIdentity.userId } : null),
    [actorId, humanIdentity?.userId],
  );
  const viewer = useMemo<BotCatalogViewer | null>(() => {
    if (activeIdentity?.kind === 'bot' && activeIdentity.id) {
      return { viewerActorType: 'bot', viewerActorId: activeIdentity.id };
    }
    return humanIdentity?.userId ? { viewerActorType: 'human', viewerActorId: humanIdentity.userId } : null;
  }, [activeIdentity?.id, activeIdentity?.kind, humanIdentity?.userId]);

  const fetch = useBotCatalogFetch({ viewer, humanBotContext, enabled });
  const [busyKeys, setBusyKeys] = useState<string[]>([]);
  const [selectedBotId, setSelectedBotId] = useState<string | null>(null);
  const [botProfile, setBotProfile] = useState<PublicBotProfile | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const runBusy = useCallback(
    async (key: string, task: () => Promise<void>, invalidTargetId?: string) => {
      if (busyKeys.includes(key)) return;
      setBusyKeys((cur) => [...cur, key]);
      try {
        await task();
      } catch (e) {
        if (e instanceof CollaborationSquareError && e.code === 'target_invalid') {
          const separator = key.indexOf(':');
          const id = invalidTargetId ?? (separator < 0 ? '' : key.slice(separator + 1));
          fetch.setBots((cur) => cur.filter((bot) => getPublicBotTargetId(bot) !== id));
          setSelectedBotId(null);
          notifyError('内容已取消公开或不可访问');
        } else {
          notifyError(getCollaborationSquareErrorMessage(e));
        }
      } finally {
        setBusyKeys((cur) => cur.filter((k) => k !== key));
      }
    },
    [busyKeys, fetch.setBots],
  );

  const primaryAction = useCallback(
    (bot: PublicBot) => {
      if (!humanBotContext) {
        notifyError('当前查看身份不可用，请刷新后重试');
        return;
      }
      const targetId = getPublicBotTargetId(bot);
      if (canStartPublicBotConversation(bot)) {
        void runBusy(
          `bot:${targetId}`,
          async () => {
            const result = await collaborationSquareBotService.openBotConversation(bot.id, humanBotContext);
            history.push(getCollaborationBotConversationUrl(bot.id, result.sessionId));
          },
          bot.id,
        );
        return;
      }
      void runBusy(
        `bot:${targetId}`,
        async () => {
          // from_actor 跟随当前角色 tab（human→userId，bot→bot id），由 viewer 推导。
          const fromActor = viewer ? { type: viewer.viewerActorType, id: viewer.viewerActorId } : undefined;
          const result = bot.friendRequestBotId
            ? await collaborationSquareBotService.requestBotFriendship(
                bot.id,
                humanBotContext,
                bot.friendRequestBotId,
                fromActor,
              )
            : await collaborationSquareBotService.requestBotFriendship(bot.id, humanBotContext, undefined, fromActor);
          fetch.setBots((cur) =>
            cur.map((item) =>
              getPublicBotTargetId(item) === targetId ? { ...item, relationshipStatus: result.status } : item,
            ),
          );
          if (result.status === 'friend') {
            const conversation = await collaborationSquareBotService.openBotConversation(bot.id, humanBotContext);
            notifySuccess('好友关系已建立，正在进入对话');
            history.push(getCollaborationBotConversationUrl(bot.id, conversation.sessionId));
          } else if (result.status === 'applying') notifySuccess('好友申请已提交');
          else notifyError('当前未建立好友关系，申请未提交');
        },
        bot.id,
      );
    },
    [humanBotContext, runBusy, fetch.setBots],
  );

  const openProfile = useCallback(
    async (bot: PublicBot) => {
      setSelectedBotId(bot.id);
      setDetailLoading(true);
      try {
        setBotProfile(await collaborationSquareService.getBotProfile(bot.id));
      } catch (e) {
        if (e instanceof CollaborationSquareError && e.code === 'target_invalid') {
          fetch.setBots((cur) => cur.filter((item) => item.id !== bot.id));
          setSelectedBotId(null);
          notifyError('内容已取消公开或不可访问');
        } else {
          notifyError(getCollaborationSquareErrorMessage(e));
        }
      } finally {
        setDetailLoading(false);
      }
    },
    [fetch.setBots],
  );

  const closeProfile = useCallback(() => {
    setSelectedBotId(null);
    setBotProfile(null);
  }, []);

  const copyText = useCallback(async (value: string, success: string) => {
    try {
      await navigator.clipboard.writeText(value);
      notifySuccess(success);
    } catch {
      notifyError('复制失败，请检查浏览器剪贴板权限');
    }
  }, []);
  const share = useCallback(
    (bot: PublicBot) => {
      const origin = typeof window === 'undefined' ? '' : window.location.origin;
      void copyText(getCollaborationSquareShareUrl(origin, 'bot', bot.id, bot.name), '分享链接已复制');
    },
    [copyText],
  );
  const copyBotId = useCallback((id: string) => void copyText(id, 'Bot UUID 已复制'), [copyText]);

  return {
    bots: fetch.bots,
    busyKeys,
    query: fetch.query,
    mode: fetch.mode,
    loading: fetch.loading,
    error: fetch.error,
    hasMore: fetch.hasMore,
    loadingMore: fetch.loadingMore,
    loadMoreError: fetch.loadMoreError,
    setQuery: fetch.setQuery,
    setMode: fetch.setMode,
    reload: fetch.reload,
    loadMore: () => void fetch.loadMore(),
    primaryAction,
    share,
    openProfile,
    closeProfile,
    selectedBotId,
    botProfile,
    detailLoading,
    copyBotId,
  };
}
