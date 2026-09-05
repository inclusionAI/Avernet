import { notifyError, notifySuccess } from '@/components/ui/notify';
import type {
  CollaborationBot,
  FriendApprovalConfig,
  PublicAudience,
  PublicConfig,
} from '@/domain/collaborationPrivacy/types';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { collaborationPrivacyService, type DirectSetting } from '@/services/collaborationPrivacy';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  buildDirectConfirmation,
  directSettingLabel,
  errorMessage,
  matchesBotIdentity,
  type Confirmation,
  type PublicationEditorState,
  type ScopeViewerState,
} from './collaborationPrivacyHelpers';

export function useCollaborationPrivacy() {
  const store = useCollaborationPrivacyStore();
  const { identity, status: identityStatus, error: identityError } = useHumanIdentity();
  const activeIdentityId = useWorkspaceStore((state) => state.activeIdentityId);
  const identityViews = useWorkspaceStore((state) => state.identities);
  const userId = identity?.userId.trim() || null;
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [publicationEditor, setPublicationEditor] = useState<PublicationEditorState | null>(null);
  const [scopeViewer, setScopeViewer] = useState<ScopeViewerState | null>(null);
  const [friendEditorBotId, setFriendEditorBotId] = useState<string | null>(null);
  const previousActiveIdentityId = useRef(activeIdentityId);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!userId) {
        store.setLoading(false);
        store.setError(identityError ?? '当前用户身份未就绪，无法加载协作权限');
        return;
      }
      store.setLoading(true);
      store.setError(null);
      try {
        store.setOverview(await collaborationPrivacyService.loadOverview(userId, signal));
      } catch (error) {
        if ((error as Error).name !== 'AbortError') store.setError(errorMessage(error));
      } finally {
        store.setLoading(false);
      }
    },
    [identityError, store.setError, store.setLoading, store.setOverview, userId],
  );

  useEffect(() => {
    const controller = new AbortController();
    if (identityStatus !== 'loading') void load(controller.signal);
    return () => {
      controller.abort();
      useCollaborationPrivacyStore.getState().reset();
    };
  }, [identityStatus, load]);

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
        config.scope === 'none' ? '公开范围已关闭，当前已立即生效' : '审批申请已提交，当前公开范围保持不变',
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
  const activeIdentity = useMemo(
    () => identityViews.find((item) => item.id === activeIdentityId) ?? null,
    [activeIdentityId, identityViews],
  );
  useEffect(() => {
    if (previousActiveIdentityId.current === activeIdentityId) return;
    previousActiveIdentityId.current = activeIdentityId;
    setConfirmation(null);
    setPublicationEditor(null);
    setScopeViewer(null);
    setFriendEditorBotId(null);
  }, [activeIdentityId]);
  const visibleBots = useMemo(() => {
    if (activeIdentity?.kind !== 'bot' || !store.overview) return [];
    return store.overview.bots.filter((bot) => matchesBotIdentity(bot.id, activeIdentity.id));
  }, [activeIdentity, store.overview]);
  return {
    ...store,
    load,
    confirmation,
    publicationEditor,
    publicationBot,
    scopeViewer,
    scopeViewerBot,
    friendEditorBot,
    activeIdentity,
    showIdentityCard: activeIdentity?.kind !== 'bot',
    visibleBots,
    refreshBot,
    toggleDirect,
    confirmDirect,
    cancelConfirmation: () => setConfirmation(null),
    openPublicationEditor: (bot: CollaborationBot, audience: PublicAudience) =>
      setPublicationEditor({ botId: bot.id, audience }),
    closePublicationEditor: () => setPublicationEditor(null),
    openScopeViewer: (bot: CollaborationBot, audience: PublicAudience) =>
      setScopeViewer({ kind: 'publication', botId: bot.id, audience }),
    openFriendScopeViewer: (bot: CollaborationBot) => setScopeViewer({ kind: 'friendApproval', botId: bot.id }),
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
