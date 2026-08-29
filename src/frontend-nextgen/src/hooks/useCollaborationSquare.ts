import { parseSquareDeepLink } from '@/domain/collaborationSquare/mapper';
import type { HumanBotActionContext, PublicBot, PublicGroup, SquareResource } from '@/domain/collaborationSquare/types';
import { useCollaborationSquareList } from '@/hooks/useCollaborationSquareList';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import {
  CollaborationSquareError,
  collaborationSquareBotService,
  collaborationSquareService,
} from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history } from '@umijs/max';
import { useCallback, useEffect, useMemo } from 'react';
import { notifyError, notifySuccess } from '@/components/ui/notify';

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}
function botConversationUrl(botId: string, sessionId: string) {
  return `/workspace?tab=chat&bot=${encodeURIComponent(botId)}&session=${encodeURIComponent(sessionId)}`;
}

function clearTargetingSearch(resource: SquareResource, id: string) {
  if (typeof window === 'undefined') return;
  const params = new URLSearchParams(window.location.search);
  if (params.get('resource') !== resource || params.get('id') !== id) return;
  params.delete('resource');
  params.delete('id');
  const search = params.toString();
  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${search ? `?${search}` : ''}${window.location.hash}`,
  );
}

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
  const load = useCollaborationSquareList({
    resource,
    humanBotContext,
    humanIdentityStatus,
    botQuery: store.botQuery,
    groupQuery: store.groupQuery,
    botSearchMode: store.botSearchMode,
    setBots: store.setBots,
    setGroups: store.setGroups,
    setLoading: store.setLoading,
    setError: store.setError,
  });

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
      clearTargetingSearch(targetResource, id);
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
        else notifyError(errorMessage(error));
      } finally {
        store.setDetailLoading(false);
      }
    },
    [handleTargetInvalid, store.setBotProfile, store.setDetailLoading, store.setSelectedBotId],
  );

  const openGroupMembers = useCallback(
    async (group: PublicGroup) => {
      store.setSelectedGroupId(group.id);
      if (group.memberListVisibility === 'count_only') return;
      store.setDetailLoading(true);
      try {
        store.setGroupMembers(await collaborationSquareService.listGroupMembers(group.id));
      } catch (error) {
        if (error instanceof CollaborationSquareError && error.code === 'target_invalid')
          handleTargetInvalid('group', group.id);
        else notifyError(errorMessage(error));
      } finally {
        store.setDetailLoading(false);
      }
    },
    [handleTargetInvalid, store.setDetailLoading, store.setGroupMembers, store.setSelectedGroupId],
  );

  useEffect(() => {
    if (store.loading || typeof window === 'undefined') return;
    const deepLink = parseSquareDeepLink(window.location.search, resource);
    if (!deepLink) return;
    if (resource === 'bot') {
      const bot = store.bots.find((item) => item.id === deepLink.id);
      if (bot) void openBotProfile(bot);
      else handleTargetInvalid('bot', deepLink.id);
    } else {
      const group = store.groups.find((item) => item.id === deepLink.id);
      if (group) void openGroupMembers(group);
      else handleTargetInvalid('group', deepLink.id);
    }
  }, [handleTargetInvalid, openBotProfile, openGroupMembers, resource, store.bots, store.groups, store.loading]);

  const runBusy = useCallback(
    async (key: string, task: () => Promise<void>) => {
      if (store.busyKeys.includes(key)) return;
      store.setBusy(key, true);
      try {
        await task();
      } catch (error) {
        if (error instanceof CollaborationSquareError && error.code === 'target_invalid') {
          const separator = key.indexOf(':');
          const kind = separator < 0 ? key : key.slice(0, separator);
          const id = separator < 0 ? '' : key.slice(separator + 1);
          handleTargetInvalid(kind === 'bot' ? 'bot' : 'group', id);
        } else notifyError(errorMessage(error));
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
      if (bot.relationshipStatus === 'friend') {
        void runBusy(`bot:${bot.id}`, async () => {
          const result = await collaborationSquareBotService.openBotConversation(bot.id, humanBotContext);
          history.push(botConversationUrl(bot.id, result.sessionId));
        });
        return;
      }
      void runBusy(`bot:${bot.id}`, async () => {
        const result = await collaborationSquareBotService.requestBotFriendship(bot.id, humanBotContext);
        store.updateBotRelationship(bot.id, result.status);
        if (result.status === 'friend') {
          const conversation = await collaborationSquareBotService.openBotConversation(bot.id, humanBotContext);
          notifySuccess('好友关系已建立，正在进入对话');
          history.push(botConversationUrl(bot.id, conversation.sessionId));
        } else if (result.status === 'applying') notifySuccess('好友申请已提交');
        else notifyError('当前未建立好友关系，申请未提交');
      });
    },
    [humanBotContext, runBusy, store.updateBotRelationship],
  );

  const createGroupSession = useCallback(
    (group: PublicGroup) => {
      void runBusy(`group:${group.id}`, async () => {
        const result = await collaborationSquareService.createGroupSession(group.id);
        notifySuccess(`新会话已创建，你将以${result.defaultRole}身份临时加入本会话`);
        history.push(
          `/workspace?sessionId=${encodeURIComponent(result.sessionId)}&memberSource=${
            result.memberSource
          }&defaultRole=${encodeURIComponent(result.defaultRole)}`,
        );
      });
    },
    [runBusy],
  );

  const copyText = useCallback(async (value: string, success: string) => {
    try {
      await navigator.clipboard.writeText(value);
      notifySuccess(success);
    } catch {
      notifyError('复制失败，请检查浏览器剪贴板权限');
    }
  }, []);
  const share = useCallback(
    (targetResource: SquareResource, id: string) => {
      const pathname = targetResource === 'bot' ? '/collaboration-square/bots' : '/collaboration-square/groups';
      const origin = typeof window === 'undefined' ? '' : window.location.origin;
      void copyText(`${origin}${pathname}?resource=${targetResource}&id=${encodeURIComponent(id)}`, '分享链接已复制');
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
    openBotProfile,
    closeBotProfile,
    openGroupMembers,
    closeGroupMembers,
    primaryBotAction,
    createGroupSession,
    copyBotId: (id: string) => copyText(id, 'Bot ID 已复制'),
    share,
  };
}
