// 协作权限「Bot 动作编排」子 Hook：从 useCollaborationPrivacy 拆出（TC-G005 文件大小）。
// 收口单项 Bot 的按钮动作与确认弹层状态：直改开关（含二次确认）、好友审批提交、
// 部门同步与组织搜索；service 编排与 toast 均在此层，父 Hook 只负责页面级拼装。
import { notifyError, notifySuccess } from '@/components/ui/notify';
import type { CollaborationBot, FriendApprovalConfig } from '@/domain/collaborationPrivacy/types';
import { collaborationPrivacyService, type DirectSetting } from '@/services/collaborationPrivacy';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import { useCallback, useState } from 'react';
import {
  buildDirectConfirmation,
  directSettingLabel,
  errorMessage,
  type Confirmation,
} from './collaborationPrivacyHelpers';

export function useCollaborationPrivacyActions({
  userId,
  identityError,
}: {
  userId: string | null;
  identityError?: string | null;
}) {
  const store = useCollaborationPrivacyStore();
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [friendEditorBotId, setFriendEditorBotId] = useState<string | null>(null);

  const runBotAction = useCallback(
    async (actionKey: string, task: () => Promise<CollaborationBot>, success: string) => {
      if (store.busyAction) return false;
      store.setBusyAction(actionKey);
      try {
        store.updateBot(await task());
        notifySuccess(success);
        return true;
      } catch (error) {
        notifyError(errorMessage(error));
        return false;
      } finally {
        store.setBusyAction(null);
      }
    },
    [store.busyAction, store.setBusyAction, store.updateBot],
  );

  const refreshBot = useCallback(
    (bot: CollaborationBot) =>
      runBotAction(`${bot.id}:refresh`, () => collaborationPrivacyService.refreshBot(bot.id), 'Bot 权限状态已刷新'),
    [runBotAction],
  );

  const executeDirect = useCallback(
    (bot: CollaborationBot, setting: DirectSetting, value: Confirmation['value']) => {
      if (setting === 'taskClaimingEnabled') {
        return runBotAction(
          `${bot.id}:taskClaimingEnabled`,
          () =>
            value
              ? collaborationPrivacyService.enableTaskClaim(bot.id)
              : collaborationPrivacyService.disableTaskClaim(bot.id),
          directSettingLabel(setting, value),
        );
      }
      return runBotAction(
        `${bot.id}:${setting}`,
        () => collaborationPrivacyService.updateDirectSetting({ botId: bot.id, setting, value }),
        directSettingLabel(setting, value),
      );
    },
    [runBotAction],
  );

  const toggleDirect = useCallback(
    (bot: CollaborationBot, setting: DirectSetting, value: Confirmation['value']) => {
      const next = buildDirectConfirmation(bot, setting, value);
      if (next) {
        setConfirmation(next);
        return;
      }
      void executeDirect(bot, setting, value);
    },
    [executeDirect],
  );

  const confirmDirect = useCallback(async () => {
    if (!confirmation) return;
    const succeeded = await executeDirect(confirmation.bot, confirmation.setting, confirmation.value);
    if (succeeded) setConfirmation(null);
  }, [confirmation, executeDirect]);

  const submitFriendApproval = useCallback(
    async (config: FriendApprovalConfig) => {
      const bot = store.overview?.bots.find((item) => item.id === friendEditorBotId);
      if (!bot) return;
      const succeeded = await runBotAction(
        `${bot.id}:friendApproval`,
        () => collaborationPrivacyService.updateFriendApproval({ botId: bot.id, config }),
        config.mode === 'none'
          ? '好友审批策略已更新为“无需审批”'
          : config.mode === 'all'
          ? '好友审批策略已更新为“全部审批”'
          : '好友审批策略已更新',
      );
      if (succeeded) setFriendEditorBotId(null);
    },
    [friendEditorBotId, runBotAction, store.overview],
  );

  const syncDepartment = useCallback(async () => {
    if (store.busyAction) return;
    store.setBusyAction('syncDepartment');
    try {
      if (!userId) {
        notifyError(identityError ?? '当前用户身份未就绪，无法同步部门信息');
        return;
      }
      const result = await collaborationPrivacyService.syncDepartment(userId);
      store.updateCurrentUser(result.identity);
      notifySuccess(result.changed ? '用户部门信息已同步' : '当前已是最新信息');
    } catch (error) {
      notifyError(errorMessage(error));
    } finally {
      store.setBusyAction(null);
    }
  }, [identityError, store.busyAction, store.setBusyAction, store.updateCurrentUser, userId]);

  const searchDepartments = useCallback(async (keyword: string, signal?: AbortSignal) => {
    return await collaborationPrivacyService.searchDepartments(keyword, signal);
  }, []);

  /** 身份切换时父 Hook 调用：清空确认弹层与好友编辑器（其余页面态由父级自行重置）。 */
  const resetDialogStates = useCallback(() => {
    setConfirmation(null);
    setFriendEditorBotId(null);
  }, []);

  return {
    confirmation,
    friendEditorBotId,
    /** 供父 Hook 复用相同 busyAction 互斥语义(公开动线等长事务)。 */
    runBotAction,
    refreshBot,
    toggleDirect,
    confirmDirect,
    cancelConfirmation: () => setConfirmation(null),
    openFriendEditor: (bot: CollaborationBot) => setFriendEditorBotId(bot.id),
    closeFriendEditor: () => setFriendEditorBotId(null),
    submitFriendApproval,
    syncDepartment,
    searchDepartments,
    resetDialogStates,
  };
}
