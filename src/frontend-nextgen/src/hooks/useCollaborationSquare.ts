import { notifyError, notifySuccess } from '@/components/ui/notify';
import {
  canStartPublicBotConversation,
  getPublicBotTargetId,
  type HumanBotActionContext,
  type PublicBot,
  type PublicGroup,
  type SquareResource,
} from '@/domain/collaborationSquare/types';
import { mapPublicBotCatalogSummaryToProfile } from '@/domain/collaborationSquare/mapper';
import { useCollaborationSquareList } from '@/hooks/useCollaborationSquareList';
import { useCollaborationSquareTask } from '@/hooks/useCollaborationSquareTask';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useSquareDeepLink } from '@/hooks/useSquareDeepLink';
import {
  CollaborationSquareError,
  collaborationSquareBotService,
  collaborationSquareGroupService,
  collaborationSquareService,
} from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import {
  clearCollaborationSquareTargetingSearch,
  getCollaborationBotConversationUrl,
  getCollaborationSquareErrorMessage,
  getCollaborationSquareShareUrl,
} from '@/utils/collaborationSquare';
import { history } from '@umijs/max';
import { useCallback, useMemo } from 'react';
import { useCreateGroupSessionFlow } from './useCreateGroupSessionFlow';

export function useCollaborationSquare(resource: SquareResource) {
  const store = useCollaborationSquareStore();
  const { identity: humanIdentity, status: humanIdentityStatus } = useHumanIdentity();
  const actorId = useWorkspaceStore(
    (state) => state.identities.find((item) => item.kind === 'user' && !item.id.startsWith('test-'))?.id ?? null,
  );
  const humanBotContext = useMemo<HumanBotActionContext | null>(
    () => (actorId && humanIdentity?.userId ? { actorId, userId: humanIdentity.userId } : null),
    [actorId, humanIdentity?.userId],
  );
  const viewer = useMemo(
    () => (humanBotContext ? { viewerActorType: 'human' as const, viewerActorId: humanBotContext.userId } : null),
    [humanBotContext],
  );
  const botGroupList = useCollaborationSquareList({
    resource,
    humanBotContext,
    humanIdentityStatus,
    botQuery: store.botQuery,
    groupQuery: store.groupQuery,
    botSearchMode: store.botSearchMode,
    viewer: viewer ?? undefined,
    gateSmartEmpty: true,
    setBots: store.setBots,
    appendBots: store.appendBots,
    setGroups: store.setGroups,
    appendGroups: store.appendGroups,
    setLoading: store.setLoading,
    setError: store.setError,
  });
  const taskView = useCollaborationSquareTask(resource);
  const { load, loadMore, hasMore, loadingMore, loadMoreError } = resource === 'task' ? taskView.list : botGroupList;

  const closeBotProfile = useCallback(() => {
    store.setSelectedBotId(null);
    store.setBotProfile(null);
  }, [store.setBotProfile, store.setSelectedBotId]);
  const closeGroupMembers = useCallback(() => {
    store.setSelectedGroupId(null);
    store.setGroupMembers([]);
  }, [store.setGroupMembers, store.setSelectedGroupId]);

  const handleTargetInvalid = useCallback(
    (targetResource: SquareResource, id: string) => {
      clearCollaborationSquareTargetingSearch(targetResource, id);
      if (targetResource === 'bot') {
        store.removeBot(id);
        closeBotProfile();
      } else {
        store.removeGroup(id);
        closeGroupMembers();
      }
      notifyError('内容已取消公开或不可访问');
    },
    [closeBotProfile, closeGroupMembers, store.removeBot, store.removeGroup],
  );

  const openBotProfile = useCallback(
    async (bot: PublicBot) => {
      store.setSelectedBotId(bot.id);
      store.setDetailLoading(true);
      try {
        store.setBotProfile(await collaborationSquareService.getBotProfile(bot.id));
      } catch (error) {
        if (error instanceof CollaborationSquareError && error.code === 'target_invalid')
          handleTargetInvalid('bot', bot.id);
        else notifyError(getCollaborationSquareErrorMessage(error));
      } finally {
        store.setDetailLoading(false);
      }
    },
    [handleTargetInvalid, store.setBotProfile, store.setDetailLoading, store.setSelectedBotId],
  );

  const openSharedBot = useCallback(
    (bot: PublicBot) => {
      store.setSelectedBotId(bot.id);
      store.setDetailLoading(false);
      store.setBotProfile(mapPublicBotCatalogSummaryToProfile(bot));
    },
    [store.setBotProfile, store.setDetailLoading, store.setSelectedBotId],
  );
  const openGroupMembers = useCallback(
    async (group: PublicGroup) => {
      store.setSelectedGroupId(group.id);
      store.setDetailLoading(true);
      try {
        // 公开群成员经群详情 participants 取得（见 adapter.listGroupMembers）。
        store.setGroupMembers(await collaborationSquareGroupService.listGroupMembers(group.id));
      } catch (error) {
        if (error instanceof CollaborationSquareError && error.code === 'target_invalid')
          handleTargetInvalid('group', group.id);
        else notifyError(getCollaborationSquareErrorMessage(error));
      } finally {
        store.setDetailLoading(false);
      }
    },
    [handleTargetInvalid, store.setDetailLoading, store.setGroupMembers, store.setSelectedGroupId],
  );

  useSquareDeepLink({
    resource,
    humanBotContext,
    viewer: viewer ?? undefined,
    openSharedBot,
    openGroupMembers,
    handleTargetInvalid,
  });

  const runBusy = useCallback(
    async (key: string, task: () => Promise<void>, invalidTargetId?: string) => {
      if (store.busyKeys.includes(key)) return;
      store.setBusy(key, true);
      try {
        await task();
      } catch (error) {
        if (error instanceof CollaborationSquareError && error.code === 'target_invalid') {
          const separator = key.indexOf(':');
          const kind = separator < 0 ? key : key.slice(0, separator);
          const id = invalidTargetId ?? (separator < 0 ? '' : key.slice(separator + 1));
          handleTargetInvalid(kind === 'bot' ? 'bot' : 'group', id);
        } else notifyError(getCollaborationSquareErrorMessage(error));
      } finally {
        store.setBusy(key, false);
      }
    },
    [handleTargetInvalid, store.busyKeys, store.setBusy],
  );

  const primaryBotAction = useCallback(
    (bot: PublicBot) => {
      if (!humanBotContext) {
        notifyError('当前用户身份不可用，请刷新后重试');
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
          const result = bot.friendRequestBotId
            ? await collaborationSquareBotService.requestBotFriendship(bot.id, humanBotContext, bot.friendRequestBotId)
            : await collaborationSquareBotService.requestBotFriendship(bot.id, humanBotContext);
          store.updateBotRelationship(targetId, result.status);
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
    [humanBotContext, runBusy, store.updateBotRelationship],
  );

  const createSessionFlow = useCreateGroupSessionFlow(humanBotContext, runBusy);
  const createGroupSession = createSessionFlow.open;

  const copyText = useCallback(async (value: string, success: string) => {
    try {
      await navigator.clipboard.writeText(value);
      notifySuccess(success);
    } catch {
      notifyError('复制失败，请检查浏览器剪贴板权限');
    }
  }, []);
  const share = useCallback(
    (targetResource: SquareResource, id: string, searchHint?: string) => {
      const origin = typeof window === 'undefined' ? '' : window.location.origin;
      void copyText(getCollaborationSquareShareUrl(origin, targetResource, id, searchHint), '分享链接已复制');
    },
    [copyText],
  );

  const visibleBots = store.bots;
  const visibleGroups = store.groups;
  const selectedGroup = store.groups.find((item) => item.id === store.selectedGroupId) ?? null;

  return {
    ...store,
    visibleBots,
    visibleGroups,
    selectedGroup,
    load,
    reload: load,
    loadMore,
    hasMore,
    loadingMore,
    loadMoreError,
    openBotProfile,
    closeBotProfile,
    openGroupMembers,
    closeGroupMembers,
    primaryBotAction,
    createGroupSession,
    openTaskDetail: taskView.openTaskDetail,
    closeTaskDetail: taskView.closeTaskDetail,
    createSessionTarget: createSessionFlow.target,
    isCreatingSession: createSessionFlow.isCreating,
    closeCreateSessionModal: createSessionFlow.close,
    submitCreateSession: createSessionFlow.submit,
    copyBotId: (id: string) => copyText(id, 'Bot UUID 已复制'),
    share,
  };
}
