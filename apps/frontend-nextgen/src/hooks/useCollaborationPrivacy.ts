import {
  resolvePublishChannel,
  toBotPublicRiskVisibility,
} from '@/domain/collaborationPrivacy/botPublicRisk';
import {
  matchesBotIdentity,
  resolveFixedBotLoadScope,
  resolvePrivacyLoadScope,
} from '@/domain/collaborationPrivacy/loadScope';
import type { CollaborationBot, PublicAudience, PublicConfig } from '@/domain/collaborationPrivacy/types';
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
import { useCollaborationPrivacyUserRisk } from './useCollaborationPrivacyUserRisk';
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
    async (signal?: AbortSignal, options?: { silent?: boolean }) => {
      const loadId = ++latestLoadId.current;
      const silent = options?.silent ?? false;
      if (!userId) {
        store.setLoading(false);
        store.setError(identityError ?? '当前用户身份未就绪，无法加载协作权限');
        return;
      }
      // silent（修复后自动刷新）只在开始时不切入整面板 loading；失败仍写 error 供行内提示，
      // finally 不跳过——否则它会使更早的非静默读取代次失效后，loading 卡死在 true。
      if (!silent) store.setLoading(true);
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
      // 先失效所有在途读取（含无 signal 的手动/修复后刷新），再 abort/reset：
      // 否则迟到的成功/失败/finally 会改写已 reset 的共享 Store（切 Bot 时覆盖新 Bot 状态）。
      latestLoadId.current += 1;
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

  // Bot 公开风险前置检测接线收敛在子 Hook(TC-G005 拆分)：仅「对用户」维度启用，
  // 外部 Bot 不参与；进入审批边沿经 silentReload 回调走本 Hook 的静默 load。
  const { userRisk, createUserRisk, blockedRiskApprovalPuid } = useCollaborationPrivacyUserRisk({
    userId,
    fixedBotId,
    isExternalBot,
    visibleBots,
    publicationBot,
    publicationEditor,
    silentReload: () => void load(undefined, { silent: true }),
  });
  const submitPublication = useCallback(
    async (config: PublicConfig, deptEntries?: Array<{ deptNo: string; deptName: string }>) => {
      if (!publicationEditor) return;
      const bot = store.overview?.bots.find((item) => item.id === publicationEditor.botId);
      if (!bot) return;
      // 对用户公开向变更走前置检测动线（design D1）：发起成功保持编辑器打开进入已发起终态；
      // 收紧 / 外部 Bot / 对 Bot 维度继续走原直改/审批通道。
      const isUserPublication = !isExternalBot && publicationEditor.audience === 'user';
      if (isUserPublication && resolvePublishChannel(config, bot.publication.user, false) === 'risk-check') {
        await createUserRisk(bot.id, toBotPublicRiskVisibility(config.scope, deptEntries));
        return;
      }
      const command = buildPublicationCommand({
        botId: bot.id,
        audience: publicationEditor.audience,
        config,
        deptEntries,
        isExternalBot,
      });
      const succeeded = await runBotAction(
        `${bot.id}:publication:${publicationEditor.audience}`,
        () =>
          collaborationPrivacyService.submitPublication(
            isUserPublication && blockedRiskApprovalPuid ? { ...command, blockedRiskApprovalPuid } : command,
          ),
        isExternalBot ? externalPublicationSaveMessage : publicationSuccessMessage(config),
      );
      if (succeeded) setPublicationEditor(null);
    },
    [isExternalBot, publicationEditor, createUserRisk, blockedRiskApprovalPuid, runBotAction, store.overview],
  );

  return {
    ...store,
    userRisk,
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
