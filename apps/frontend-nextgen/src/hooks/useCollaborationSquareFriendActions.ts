import { getCapabilities } from '@/capabilities';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import {
  getPublicBotActionKey,
  getPublicBotTargetId,
  resolvePublicBotPrimaryAction,
  type BotRelationshipStatus,
  type FriendRequestActor,
  type HumanBotActionContext,
  type PublicBot,
  type SquareResource,
} from '@/domain/collaborationSquare/types';
import { CollaborationSquareError, collaborationSquareBotService } from '@/services/collaborationSquare';
import { getCollaborationBotConversationUrl } from '@/utils/collaborationSquare';
import { safeReportError } from '@/utils/errorToast';
import { history } from '@umijs/max';
import { useCallback, useLayoutEffect, useRef } from 'react';
import {
  useCollaborationActorOperationScope,
  type CollaborationOperationGuard,
} from './useCollaborationActorOperationScope';

export interface UseCollaborationSquareFriendActionsOptions {
  resource: SquareResource;
  activeActor: FriendRequestActor | null;
  humanBotContext: HumanBotActionContext | null;
  /** 目标失效（target_invalid）时的既有清卡片处理。 */
  handleTargetInvalid: (targetResource: SquareResource, id: string) => void;
  /** 广场 busy 状态（busy 防重）与关系回填的既有 store 接缝。 */
  busyKeys: string[];
  setBusy: (key: string, busy: boolean) => void;
  updateBotRelationship: (targetId: string, status: BotRelationshipStatus) => void;
}

/**
 * 好友操作的代次隔离与 Human 建聊开关（bcs-standalone-friend-discovery Task 4）。
 *
 * 拆自 useCollaborationSquare 以控 Hook 体积。scopeKey 以 JSON.stringify 编码
 * [登录 userId, resource, actor.type, actor.id]；角色变化／卸载的同步 cleanup 清理本 Hook
 * 当前代次持有的 busy keys（局部 ref 记录），不等待旧请求 finally，也不全局 reset 其它模块
 * 状态；新代次重新建立自己的 busy 归属。每次 await 后与 catch/finally 先核验 guard，
 * AbortController 只取消客户端等待，不代表服务端写已回滚。群操作不经本 Hook，语义不变。
 */
export function useCollaborationSquareFriendActions({
  resource,
  activeActor,
  humanBotContext,
  handleTargetInvalid,
  busyKeys,
  setBusy,
  updateBotRelationship,
}: UseCollaborationSquareFriendActionsOptions) {
  const operationScopeKey = JSON.stringify([
    humanBotContext?.userId ?? '',
    resource,
    activeActor?.type ?? '',
    activeActor?.id ?? '',
  ]);
  const captureOperation = useCollaborationActorOperationScope(operationScopeKey);
  const scopedBusyOwnersRef = useRef(new Map<string, CollaborationOperationGuard>());

  useLayoutEffect(() => {
    const owners = scopedBusyOwnersRef.current;
    return () => {
      for (const [key, isCurrent] of owners) {
        if (!isCurrent()) setBusy(key, false);
      }
      owners.clear();
    };
  }, [operationScopeKey, setBusy]);

  const runScopedFriendAction = useCallback(
    async (invalidTargetId: string, busyKey: string, task: (isCurrent: CollaborationOperationGuard) => Promise<void>) => {
      if (busyKeys.includes(busyKey)) return;
      const isCurrent = captureOperation();
      setBusy(busyKey, true);
      scopedBusyOwnersRef.current.set(busyKey, isCurrent);
      try {
        await task(isCurrent);
      } catch (error) {
        if (!isCurrent()) return;
        if (error instanceof CollaborationSquareError && error.code === 'target_invalid') {
          handleTargetInvalid('bot', invalidTargetId);
        } else if (error instanceof CollaborationSquareError && error.code === 'duplicate_action') {
          // A→B→A 后旧代次申请仍在途：service 的 inFlight 去重不是真实失败，提示仍在处理即可。
          notifyError('上一次提交仍在处理中，请稍候');
        } else safeReportError(error);
      } finally {
        if (isCurrent()) {
          scopedBusyOwnersRef.current.delete(busyKey);
          setBusy(busyKey, false);
        }
      }
    },
    [busyKeys, captureOperation, handleTargetInvalid, setBusy],
  );

  // 公开 Bot 卡片主操作：好友申请／已有好友打开私聊。Backend 关闭（BCS-only）时
  // resolvePublicBotPrimaryAction 把 open_human_bot_conversation 转为「已是好友」显示，
  // 建聊分支兜底再挡一道；开启时延续既有建聊，失败提示不回滚已建立的好友关系。
  const primaryBotAction = useCallback(
    (bot: PublicBot) => {
      if (!humanBotContext || !activeActor) {
        notifyError('当前工作身份不可用，请刷新后重试');
        return;
      }
      const targetId = getPublicBotTargetId(bot);
      const backendChatEnabled = getCapabilities().getPublicBotBackendEnabled();
      const action = resolvePublicBotPrimaryAction({
        activeActor,
        targetActorId: targetId,
        relationshipStatus: bot.relationshipStatus,
        isOwnedByLoggedInUser: bot.isOwnedByLoggedInUser,
        backendChatEnabled,
      });
      if (action === 'applying' || action === 'friendship_established' || action === 'self_target') return;
      const busyKey = getPublicBotActionKey(activeActor, targetId);
      if (action === 'open_human_bot_conversation') {
        if (!backendChatEnabled) return;
        void runScopedFriendAction(bot.id, busyKey, async (isCurrent) => {
          const result = await collaborationSquareBotService.openBotConversation(bot.id, humanBotContext, {
            isOwnedByLoggedInUser: Boolean(bot.isOwnedByLoggedInUser),
          });
          if (!isCurrent()) return;
          history.push(getCollaborationBotConversationUrl(bot.id, result.sessionId));
        });
        return;
      }
      void runScopedFriendAction(bot.id, busyKey, async (isCurrent) => {
        const result = bot.friendRequestBotId
          ? await collaborationSquareBotService.requestBotFriendship(
              bot.id,
              humanBotContext,
              bot.friendRequestBotId,
              activeActor,
            )
          : await collaborationSquareBotService.requestBotFriendship(bot.id, humanBotContext, undefined, activeActor);
        if (!isCurrent()) return;
        updateBotRelationship(targetId, result.status);
        if (result.status === 'friend') {
          notifySuccess('好友关系已建立');
          // 仅配置 Backend 增强时为 Human 继续创建私聊并跳转；建聊失败不回滚好友结果。
          if (activeActor.type === 'human' && getCapabilities().getPublicBotBackendEnabled()) {
            try {
              const conversation = await collaborationSquareBotService.openBotConversation(bot.id, humanBotContext);
              if (!isCurrent()) return;
              history.push(getCollaborationBotConversationUrl(bot.id, conversation.sessionId));
            } catch {
              if (isCurrent()) notifyError('已成为好友，但暂未进入对话');
            }
          }
        } else if (result.status === 'applying') notifySuccess('好友申请已提交');
        else notifyError('当前未建立好友关系，申请未提交');
      });
    },
    [activeActor, humanBotContext, runScopedFriendAction, updateBotRelationship],
  );

  return primaryBotAction;
}
