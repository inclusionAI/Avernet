import { notifyError } from '@/components/ui/notify';
import type { PublicBot, SquareResource } from '@/domain/collaborationSquare/types';
import { useCollaborationSquareActorContext } from '@/hooks/useCollaborationSquareActorContext';
import { useCollaborationSquareClipboardActions } from '@/hooks/useCollaborationSquareClipboardActions';
import { useCollaborationSquareFriendActions } from '@/hooks/useCollaborationSquareFriendActions';
import { useCollaborationSquareList } from '@/hooks/useCollaborationSquareList';
import { useCollaborationSquareTask } from '@/hooks/useCollaborationSquareTask';
import { useSquareDeepLink } from '@/hooks/useSquareDeepLink';
import { useSquareGroupMembers } from '@/hooks/useSquareGroupMembers';
import {
  CollaborationSquareError,
  collaborationSquareBotService,
} from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import {
  clearCollaborationSquareTargetingSearch,
  getCollaborationSquareErrorMessage,
} from '@/utils/collaborationSquare';
import { useCallback } from 'react';
import { useCreateGroupSessionFlow } from './useCreateGroupSessionFlow';

export function useCollaborationSquare(resource: SquareResource) {
  const store = useCollaborationSquareStore();
  const { humanIdentityStatus, humanBotContext, viewer, activeActor, activeActorName } = useCollaborationSquareActorContext(
    resource,
    store.reset,
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
    store.setDetailLoading(false);
  }, [store.setDetailLoading, store.setSelectedGroupId]);

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
      notifyError('内容已取消公开或对当前账号不可见');
    },
    [closeBotProfile, closeGroupMembers, store.removeBot, store.removeGroup],
  );

  const openBotProfile = useCallback(
    async (bot: PublicBot) => {
      store.setSelectedBotId(bot.id);
      store.setDetailLoading(true);
      try {
        // 画像以所点 PublicBot 摘要为兜底（CollaborationSquareApiAdapter）：
        // Backend 增强存在时由 profile 端点补齐引擎/已公开能力，未配置或增强失败时
        // 直接返回候选项构建的安全摘要，申请弹窗仍可发起申请。
        store.setBotProfile(await collaborationSquareBotService.getBotProfile(bot));
      } catch (error) {
        // 画像/已公开能力详情拉取失败（bot_uuid 不在公开目录 404、网络异常等）不做目标失效：
        // 卡片保留、申请弹窗保持打开，按「无已公开能力」版式（dmore artboard-003）展示摘要并发起申请。
        // 失败改为在弹窗内提示（mapProfileError 已 cancel httpClient 默认 sonner 投递），不再 toast。
        store.setBotProfile(null);
        store.setBotProfileError(
          error instanceof CollaborationSquareError ? error.message : 'Bot 画像加载失败，请稍后重试',
        );
      } finally {
        store.setDetailLoading(false);
      }
    },
    [store.setBotProfile, store.setBotProfileError, store.setDetailLoading, store.setSelectedBotId],
  );

  const openGroupMembers = useSquareGroupMembers(
    handleTargetInvalid,
    `${resource}:${humanBotContext?.userId ?? ''}:${humanBotContext?.actorId ?? ''}`,
  );

  useSquareDeepLink({
    resource,
    hasMoreRef: botGroupList.hasMoreRef,
    loadMoreRef: botGroupList.loadMoreRef,
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

  // Task 4：好友操作（申请／已有好友打开私聊）的代次隔离与 Human 建聊开关拆至
  // useCollaborationSquareFriendActions（控制本 Hook 体积）；群操作沿用 runBusy，语义不变。
  const primaryBotAction = useCollaborationSquareFriendActions({
    resource,
    activeActor,
    humanBotContext,
    handleTargetInvalid,
    busyKeys: store.busyKeys,
    setBusy: store.setBusy,
    updateBotRelationship: store.updateBotRelationship,
  });

  const createSessionFlow = useCreateGroupSessionFlow(humanBotContext, runBusy);
  const createGroupSession = createSessionFlow.open;

  const { copyBotId, share } = useCollaborationSquareClipboardActions();

  const visibleBots = store.bots;
  const visibleGroups = store.groups;
  const selectedGroup = store.groups.find((item) => item.id === store.selectedGroupId) ?? null;

  return {
    ...store,
    activeActor,
    activeActorName,
    humanBotContext,
    visibleBots,
    visibleGroups,
    selectedGroup,
    searchResultMode: botGroupList.searchResultMode,
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
    copyBotId,
    share,
  };
}
