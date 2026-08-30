import type {
  CollaborationBot,
  FriendApprovalConfig,
  PublicAudience,
  PublicConfig,
} from '@/domain/collaborationPrivacy/types';
import { collaborationPrivacyService, type DirectSetting } from '@/services/collaborationPrivacy';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { notifyError, notifySuccess } from '@/components/ui/notify';

interface Confirmation {
  bot: CollaborationBot;
  setting: DirectSetting;
  value: boolean | 'online' | 'hidden';
  title: string;
  description: string;
}
interface PublicationEditorState {
  botId: string;
  audience: PublicAudience;
}
interface ScopeViewerState {
  botId: string;
  audience: PublicAudience;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}

export function useCollaborationPrivacy() {
  const store = useCollaborationPrivacyStore();
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [publicationEditor, setPublicationEditor] = useState<PublicationEditorState | null>(null);
  const [scopeViewer, setScopeViewer] = useState<ScopeViewerState | null>(null);
  const [friendEditorBotId, setFriendEditorBotId] = useState<string | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      store.setLoading(true);
      store.setError(null);
      try {
        store.setOverview(await collaborationPrivacyService.loadOverview(signal));
      } catch (error) {
        if ((error as Error).name !== 'AbortError') store.setError(errorMessage(error));
      } finally {
        store.setLoading(false);
      }
    },
    [store.setError, store.setLoading, store.setOverview],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      controller.abort();
      useCollaborationPrivacyStore.getState().reset();
    };
  }, [load]);

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

  const executeDirect = useCallback(
    (bot: CollaborationBot, setting: DirectSetting, value: Confirmation['value']) => {
      const labels: Record<DirectSetting, string> = {
        collaborationStatus: value === 'online' ? '已开启参与协作群聊' : '已停止参与协作群聊',
        profilePublic: value ? '已公开 Bot 画像' : '已关闭 Bot 画像公开',
        taskClaimingEnabled: value ? '已开启任务认领' : '已关闭任务认领',
        dreamModelEnabled: value ? '已开启 Dream Model' : '已关闭 Dream Model',
      };
      return runBotAction(
        `${bot.id}:${setting}`,
        () => collaborationPrivacyService.updateDirectSetting({ botId: bot.id, setting, value }),
        labels[setting],
      );
    },
    [runBotAction],
  );

  const toggleDirect = useCallback(
    (bot: CollaborationBot, setting: DirectSetting, value: Confirmation['value']) => {
      if (setting === 'collaborationStatus' || setting === 'profilePublic') {
        const target =
          setting === 'collaborationStatus'
            ? value === 'online'
              ? '允许参与协作群聊'
              : '停止参与协作群聊'
            : value
            ? '公开 Bot 画像'
            : '关闭 Bot 画像公开';
        setConfirmation({
          bot,
          setting,
          value,
          title: `确认${target}？`,
          description:
            setting === 'collaborationStatus'
              ? value === 'online'
                ? `${bot.name} 开启后可加入新协作群，并在已加入的协作群会话中回复消息。好友单聊不受影响。`
                : `${bot.name} 关闭后无法加入新协作群，并停止在已加入的协作群会话中回复消息。好友单聊不受影响。`
              : `${bot.name} 的画像公开状态将影响发现、推荐与协作匹配。`,
        });
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

  const submitPublication = useCallback(
    async (config: PublicConfig, deptEntries?: Array<{ deptNo: string; deptName: string }>) => {
      if (!publicationEditor) return;
      const bot = store.overview?.bots.find((item) => item.id === publicationEditor.botId);
      if (!bot) return;
      const succeeded = await runBotAction(
        `${bot.id}:publication:${publicationEditor.audience}`,
        () =>
          collaborationPrivacyService.submitPublication({
            botId: bot.id,
            audience: publicationEditor.audience,
            config,
            deptEntries,
          }),
        '审批申请已提交，当前公开范围保持不变',
      );
      if (succeeded) setPublicationEditor(null);
    },
    [publicationEditor, runBotAction, store.overview],
  );

  const submitFriendApproval = useCallback(
    async (config: FriendApprovalConfig) => {
      const bot = store.overview?.bots.find((item) => item.id === friendEditorBotId);
      if (!bot) return;
      const succeeded = await runBotAction(
        `${bot.id}:friendApproval`,
        () => collaborationPrivacyService.updateFriendApproval({ botId: bot.id, config }),
        '好友审批策略已更新',
      );
      if (succeeded) setFriendEditorBotId(null);
    },
    [friendEditorBotId, runBotAction, store.overview],
  );

  const syncDepartment = useCallback(async () => {
    if (store.busyAction) return;
    store.setBusyAction('syncDepartment');
    try {
      const result = await collaborationPrivacyService.syncDepartment();
      store.updateCurrentUser(result.identity);
      notifySuccess(result.changed ? '用户部门信息已同步' : '当前已是最新信息');
    } catch (error) {
      notifyError(errorMessage(error));
    } finally {
      store.setBusyAction(null);
    }
  }, [store.busyAction, store.setBusyAction, store.updateCurrentUser]);

  const searchDepartments = useCallback(async (keyword: string, signal?: AbortSignal) => {
    return await collaborationPrivacyService.searchDepartments(keyword, signal);
  }, []);

  const copyBotId = useCallback(async (botId: string) => {
    try {
      await navigator.clipboard.writeText(botId);
      notifySuccess('Bot ID 已复制');
    } catch {
      notifyError(`复制失败，请手动复制：${botId}`);
    }
  }, []);

  const publicationBot = useMemo(
    () => store.overview?.bots.find((bot) => bot.id === publicationEditor?.botId),
    [publicationEditor?.botId, store.overview],
  );
  const friendEditorBot = useMemo(
    () => store.overview?.bots.find((bot) => bot.id === friendEditorBotId),
    [friendEditorBotId, store.overview],
  );
  const scopeViewerBot = useMemo(
    () => store.overview?.bots.find((bot) => bot.id === scopeViewer?.botId),
    [scopeViewer?.botId, store.overview],
  );

  return {
    ...store,
    load,
    confirmation,
    publicationEditor,
    publicationBot,
    scopeViewer,
    scopeViewerBot,
    friendEditorBot,
    toggleDirect,
    confirmDirect,
    cancelConfirmation: () => setConfirmation(null),
    openPublicationEditor: (bot: CollaborationBot, audience: PublicAudience) =>
      setPublicationEditor({ botId: bot.id, audience }),
    closePublicationEditor: () => setPublicationEditor(null),
    openScopeViewer: (bot: CollaborationBot, audience: PublicAudience) => setScopeViewer({ botId: bot.id, audience }),
    closeScopeViewer: () => setScopeViewer(null),
    submitPublication,
    openFriendEditor: (bot: CollaborationBot) => setFriendEditorBotId(bot.id),
    closeFriendEditor: () => setFriendEditorBotId(null),
    submitFriendApproval,
    syncDepartment,
    searchDepartments,
    copyBotId,
  };
}
