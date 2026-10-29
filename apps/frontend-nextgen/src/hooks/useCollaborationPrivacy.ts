import { notifySuccess } from '@/components/ui/notify';
import { resolvePublishChannel, toBotPublicRiskVisibility } from '@/domain/collaborationPrivacy/botPublicRisk';
import {
  matchesBotIdentity,
  resolveFixedBotLoadScope,
  resolvePrivacyLoadScope,
} from '@/domain/collaborationPrivacy/loadScope';
import type { CollaborationBot, PublicAudience, PublicConfig } from '@/domain/collaborationPrivacy/types';
import { useBotPublicRisk } from '@/hooks/useBotPublicRisk';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { collaborationPrivacyService } from '@/services/collaborationPrivacy';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  buildPublicationCommand,
  errorMessage,
  externalPublicationSaveMessage,
  publicationSuccessMessage,
  type PublicationEditorState,
  type ScopeViewerState,
} from './collaborationPrivacyHelpers';
import { useCollaborationPrivacyActions } from './useCollaborationPrivacyActions';
import { useCopyBotId } from './useCopyBotId';

/** 协作权限编排 Hook：默认按工作身份推导加载范围；传 fixedBotId 时按指定 Bot 弹窗加载（collab-permission-entry-migration）。isExternalBot 由外部 Bot 入口显式传入。 */
export function useCollaborationPrivacy({
  fixedBotId,
  isExternalBot = false,
}: { fixedBotId?: string; isExternalBot?: boolean } = {}) {
  const store = useCollaborationPrivacyStore();
  const { identity, status: identityStatus, error: identityError } = useHumanIdentity();
  const activeIdentityId = useWorkspaceStore((state) => state.activeIdentityId);
  const identityViews = useWorkspaceStore((state) => state.identities);
  const userId = identity?.userId.trim() || null;
  const [publicationEditor, setPublicationEditor] = useState<PublicationEditorState | null>(null);
  const [scopeViewer, setScopeViewer] = useState<ScopeViewerState | null>(null);
  const previousActiveIdentityId = useRef(activeIdentityId);
  const latestLoadId = useRef(0);
  const activeIdentity = useMemo(
    () => identityViews.find((item) => item.id === activeIdentityId) ?? null,
    [activeIdentityId, identityViews],
  );
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const loadId = ++latestLoadId.current;
      if (!userId) {
        store.setLoading(false);
        store.setError(identityError ?? '当前用户身份未就绪，无法加载协作权限');
        return;
      }
      store.setLoading(true);
      store.setError(null);
      try {
        const loadScope = fixedBotId
          ? resolveFixedBotLoadScope(fixedBotId, isExternalBot)
          : resolvePrivacyLoadScope(activeIdentity);
        const overview = await collaborationPrivacyService.loadOverview(userId, signal, loadScope);
        if (loadId === latestLoadId.current && !signal?.aborted) store.setOverview(overview);
      } catch (error) {
        if (loadId === latestLoadId.current && (error as Error).name !== 'AbortError')
          store.setError(errorMessage(error));
      } finally {
        if (loadId === latestLoadId.current) store.setLoading(false);
      }
    },
    [
      activeIdentity,
      fixedBotId,
      identityError,
      isExternalBot,
      store.setError,
      store.setLoading,
      store.setOverview,
      userId,
    ],
  );
  useEffect(() => {
    const controller = new AbortController();
    if (identityStatus !== 'loading') void load(controller.signal);
    return () => {
      controller.abort();
      useCollaborationPrivacyStore.getState().reset();
    };
  }, [identityStatus, load]);
  // 单项 Bot 动作编排与确认/好友编辑弹层状态收敛在子 Hook(TC-G005 拆分)。
  const {
    confirmation,
    friendEditorBotId,
    runBotAction,
    refreshBot,
    toggleDirect,
    confirmDirect,
    cancelConfirmation,
    openFriendEditor,
    closeFriendEditor,
    submitFriendApproval,
    syncDepartment,
    searchDepartments,
    resetDialogStates,
  } = useCollaborationPrivacyActions({ userId, identityError });
  const copyBotId = useCopyBotId();
  const botsById = useMemo(
    () => new Map((store.overview?.bots ?? []).map((bot) => [bot.id, bot] as const)),
    [store.overview],
  );
  const publicationBot = publicationEditor ? botsById.get(publicationEditor.botId) : undefined;
  const friendEditorBot = friendEditorBotId ? botsById.get(friendEditorBotId) : undefined;
  const scopeViewerBot = scopeViewer ? botsById.get(scopeViewer.botId) : undefined;
  useEffect(() => {
    if (previousActiveIdentityId.current === activeIdentityId) return;
    previousActiveIdentityId.current = activeIdentityId;
    resetDialogStates();
    setPublicationEditor(null);
    setScopeViewer(null);
  }, [activeIdentityId, resetDialogStates]);
  const visibleBots = useMemo(() => {
    if (!store.overview) return [];
    if (fixedBotId) return store.overview.bots.filter((bot) => matchesBotIdentity(bot.id, fixedBotId));
    if (activeIdentity?.kind !== 'bot') return [];
    return store.overview.bots.filter((bot) => matchesBotIdentity(bot.id, activeIdentity.id));
  }, [activeIdentity, fixedBotId, store.overview]);

  // Bot 公开风险前置检测（bot-public-risk-check，design D3/D4）：仅「对用户」维度启用。
  // 通用配置弹窗（fixedBotId 模式）打开期常开（行状态需要）；页面模式只在编辑器打开期活跃。
  const riskBotId = publicationBot?.id ?? visibleBots[0]?.id;
  const riskEnabled = Boolean(riskBotId && userId && (fixedBotId || publicationEditor));
  const risk = useBotPublicRisk({ botId: riskBotId, userId, publicScope: 'user', enabled: riskEnabled });
  useEffect(() => {
    if (riskEnabled) void risk.queryCurrent();
    // 打开弹窗/编辑器时恢复在途申请状态（spec「通用配置行在途状态」）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [riskEnabled, riskBotId, userId]);
  const submitPublication = useCallback(
    async (config: PublicConfig, deptEntries?: Array<{ deptNo: string; deptName: string }>) => {
      if (!publicationEditor) return;
      const bot = store.overview?.bots.find((item) => item.id === publicationEditor.botId);
      if (!bot) return;
      // 对用户公开向变更走前置检测动线（design D1）：发起成功保持编辑器打开进入已发起终态；
      // 收紧 / 外部 Bot / 对 Bot 维度继续走原直改/审批通道。
      const isUserPublication = !isExternalBot && publicationEditor.audience === 'user';
      if (isUserPublication && resolvePublishChannel(config, bot.publication.user, false) === 'risk-check') {
        const actionKey = `${bot.id}:publication:${publicationEditor.audience}`;
        if (store.busyAction) return;
        store.setBusyAction(actionKey);
        try {
          const record = await risk.create(toBotPublicRiskVisibility(config.scope, deptEntries));
          if (record) notifySuccess('Bot公开风险前置检测已发起');
        } finally {
          store.setBusyAction(null);
        }
        return;
      }
      const succeeded = await runBotAction(
        `${bot.id}:publication:${publicationEditor.audience}`,
        () =>
          collaborationPrivacyService.submitPublication(
            buildPublicationCommand({
              botId: bot.id,
              audience: publicationEditor.audience,
              config,
              deptEntries,
              isExternalBot,
            }),
          ),
        isExternalBot ? externalPublicationSaveMessage : publicationSuccessMessage(config),
      );
      if (succeeded) setPublicationEditor(null);
    },
    // risk.create 引用稳定（useCallback 于 useBotPublicRisk），依赖随 currentRecord 变化由 hook 内部消化
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isExternalBot, publicationEditor, risk.create, runBotAction, store.overview],
  );

  return {
    ...store,
    userRisk: {
      uiState: risk.uiState,
      record: risk.currentRecord,
      isCreating: risk.isCreating,
      refreshCurrent: risk.refreshCurrent,
      isRefreshing: risk.isRefreshing,
    },
    load,
    confirmation,
    publicationEditor,
    publicationBot,
    scopeViewer,
    scopeViewerBot,
    friendEditorBot,
    activeIdentity,
    showIdentityCard: fixedBotId ? false : activeIdentity?.kind !== 'bot',
    visibleBots,
    refreshBot,
    toggleDirect,
    confirmDirect,
    cancelConfirmation,
    openPublicationEditor: (bot: CollaborationBot, audience: PublicAudience) =>
      setPublicationEditor({ botId: bot.id, audience }),
    closePublicationEditor: () => setPublicationEditor(null),
    openScopeViewer: (bot: CollaborationBot, audience: PublicAudience) =>
      setScopeViewer({ kind: 'publication', botId: bot.id, audience }),
    openFriendScopeViewer: (bot: CollaborationBot) => setScopeViewer({ kind: 'friendApproval', botId: bot.id }),
    closeScopeViewer: () => setScopeViewer(null),
    submitPublication,
    openFriendEditor,
    closeFriendEditor,
    submitFriendApproval,
    syncDepartment,
    searchDepartments,
    copyBotId,
  };
}
