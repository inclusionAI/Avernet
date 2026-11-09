/**
 * useChatHeaderPanels —— 对话页头部五图标域的开合编排（chat-header-panels）。
 *
 * 五图标（dmore 实测语义）：
 * - 收藏会话：动作开关，复用 useConversationFavorites.toggleFavorite；
 * - 会话管理 / 历史消息：右缘面板互斥开合（新面板打开关闭旧面板）；
 * - 资源管理：收敛既有会话文件副屏（openFileDrawer / closeFileDrawer，同一容器）；
 * - 副屏：closePanel 关闭；打开走 reopenSidePaneTabsFromMessages 按消息标记幂等重建
 *   （sidePaneOpen 经 ChatLayoutPanelSlot 承接 SDK onIsOpenChange 保持为真实开合态——含
 *   消息渲染器挂载即 openTab 的「默认展开」；toggle 仅乐观翻转，真实态事件随后纠偏；
 *   会话切换复位——useBotChat 同边界 closePanelForce；无 <AixUI type="panel"> 内容时
 *   图标禁用，防 autoCloseWhenEmpty 弹回）；右缘域面板开合互斥（让位收起/全收回开/
 *   真实态收面板，修复侧边栏被悬浮副屏掩盖）统一委托 sidePanePanelMutex。
 * 历史搜索状态下沉同目录 sessionHistorySearchModel.ts（useMemo 前端过滤，Service 双源）。
 */
import type { ConversationBotSection } from '@/domain/conversation';
import {
  markTaskNavigationRouteHydrated,
  shouldPreserveTaskPanelForSession,
} from '@/services/tasks/taskConversationNavigation';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import type { ChatMessage, PanelHandle } from '@tc-chat/core';
import { useSessionTaskPanelBootstrap } from '@/hooks/useSessionTaskPanelBootstrap';
import { useSessionTaskPanelTab } from '@/hooks/useSessionTaskPanelTab';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSidePanePanelMutex } from './sidePanePanelMutex';
import { useSessionHistorySearchModel } from './sessionHistorySearchModel';
import type { UseSessionHistorySearchModel } from './sessionHistorySearchModel';
import { reopenSidePaneTabsFromMessages, sessionHasSidePaneContent } from './sidePaneContent';

// 副屏内容判定与幂等重建已下沉至同目录 sidePaneContent.ts（工程红线：Hook 单文件 ≤250 行）；
// 保留再导出兼容既有导入面（useGroupChatHeaderPanels / 测试用例）。
export { reopenSidePaneTabsFromMessages, sessionHasSidePaneContent } from './sidePaneContent';
// 历史搜索模型同边界下沉 sessionHistorySearchModel.ts；类型再导出保持既有导入面。
export type { UseSessionHistorySearchModel } from './sessionHistorySearchModel';

/** 头部右侧承载的右缘面板类型（资源面板走既有文件副屏，不在此枚举内）。 */
export type ChatHeaderPanelKind = 'detail' | 'history';

export interface ChatHeaderFavoritesModel {
  favorite: boolean;
  disabled: boolean;
  onToggle: () => void;
}

export interface UseChatHeaderPanelsOptions {
  session: BotChatSessionView | null;
  /** 当前登录用户任务列表查询口径。 */
  userId: string | null;
  messages: ChatMessage[];
  /** 副屏 handle（与 ChatPanel panelRef 同源）。 */
  panelRef: React.RefObject<PanelHandle | null>;
  /** 当前选中会话的目录 section + botId（收藏切换需要）。 */
  section: ConversationBotSection;
  botId: string | null;
  /** 收藏模型（页面既有 useConversationSessions.favorites）。 */
  toggleFavorite: (botId: string, section: ConversationBotSection, sessionId: string) => Promise<boolean>;
  favoritePending: (botId: string, section: ConversationBotSection, sessionId: string) => boolean;
  /** 资源面板 = 既有会话文件副屏。 */
  openFileDrawer: () => void;
  closeFileDrawer: () => void;
  fileDrawerOpen: boolean;
  /** 是否启用任务副屏及其进入会话自动恢复逻辑。 */
  sidePaneEnabled?: boolean;
  /** 普通单聊先完成历史读取，再恢复任务副屏，避免首屏并发全量任务查询。 */
  historyReady?: boolean;
}

export interface UseChatHeaderPanelsResult {
  /** 当前互斥开合中的面板；资源面板开合用 fileDrawerOpen 表达。 */
  openPanel: ChatHeaderPanelKind | null;
  togglePanel: (kind: ChatHeaderPanelKind) => void;
  closePanel: () => void;
  /** 右缘域面板（资源抽屉含）即将展开前的副屏让位：收起副屏并记录回开标记，面板全收后自动回开。 */
  dismissSidePaneForPanels: () => void;
  /** 关闭右缘面板与文件副屏（切换会话等场景复位）。 */
  resetAll: () => void;
  favorites: ChatHeaderFavoritesModel;
  /** 副屏真实开合态（ChatLayoutPanelSlot onIsOpenChange 同步；toggle 乐观翻转其后纠偏）。 */
  sidePaneOpen: boolean;
  /** 会话无副屏内容（消息无 <AixUI type="panel">）时 Monitor 图标禁用，toggle 空转。 */
  sidePaneDisabled: boolean;
  toggleSidePane: () => void;
  /** 真实开合同步入口（接线至 ChatLayoutPanelSlot onOpenStateChange）。 */
  onSidePaneIsOpenChange: (open: boolean) => void;
  historySearch: UseSessionHistorySearchModel;
  /** 点击搜索结果后待定位高亮的 messageId（3s 后自动清除）。 */
  highlightMessageId: string | null;
  locateMessage: (messageId: string) => void;
}

const HIGHLIGHT_TIMEOUT_MS = 3000;

export function useChatHeaderPanels(options: UseChatHeaderPanelsOptions): UseChatHeaderPanelsResult {
  const { session, messages, panelRef } = options;
  const sessionId = session?.sessionId ?? null;

  const sidePaneEnabled = options.sidePaneEnabled !== false;
  useSessionTaskPanelBootstrap({
    enabled: options.historyReady !== false,
    sessionId: sidePaneEnabled ? sessionId : null,
    userId: sidePaneEnabled ? options.userId : null,
    panelRef,
  });

  const taskTab = useSessionTaskPanelTab(sessionId);

  const [openPanel, setOpenPanel] = useState<ChatHeaderPanelKind | null>(null);
  // 副屏开合态：默认 false，经 ChatLayoutPanelSlot 承接 SDK onIsOpenChange 的真实态事件同步
  // （含渲染器「挂载即 openTab」的默认展开），toggle 仅乐观翻转、随后由真实态事件纠偏。
  const [sidePaneOpen, setSidePaneOpen] = useState(false);
  const [highlightMessageId, setHighlightMessageId] = useState<string | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeHeaderPanels = useCallback(() => {
    setOpenPanel(null);
    options.closeFileDrawer();
  }, [options.closeFileDrawer]);
  // 右缘域面板 × 副屏互斥：副屏为 AppShell 悬浮层，展开后整条盖住右缘面板让位区——委托互斥 Hook。
  const sidePaneMutex = useSidePanePanelMutex({
    panelRef,
    sidePaneOpen,
    panelsOpen: openPanel !== null || options.fileDrawerOpen,
    closePanels: closeHeaderPanels,
    onMirrorChange: setSidePaneOpen,
  });

  // 互斥开合：再次点击同一面板关闭；资源文件副屏与本组右缘面板同属头部域，一并互斥。
  const togglePanel = useCallback(
    (kind: ChatHeaderPanelKind) => {
      setOpenPanel((current) => {
        const next = current === kind ? null : kind;
        if (next !== null) {
          options.closeFileDrawer();
          sidePaneMutex.dismissSidePaneForPanels();
        }
        return next;
      });
    },
    [options, sidePaneMutex],
  );
  const closePanel = useCallback(() => setOpenPanel(null), []);
  const resetAll = useCallback(() => {
    setOpenPanel(null);
    options.closeFileDrawer();
  }, [options]);

  // 会话切换即复位开合态（面板内容按当前会话呈现）。
  const lastSessionIdRef = useRef<string | null>(sessionId);
  const [sessionEpoch, setSessionEpoch] = useState(0);
  if (lastSessionIdRef.current !== sessionId) {
    lastSessionIdRef.current = sessionId;
    setOpenPanel(null);
    setHighlightMessageId(null);
    // 副屏同步复位（兜底）：useBotChat 在同边界 closePanelForce 强关面板并清空 tabs，
    // closePanelForce 经 onIsOpenChange(false) 已同步真实态；此复位防面板随舞台切换卸载
    // 而无事件发出的脱钩场景（与群侧孪生 useGroupChatHeaderPanels 一致）。
    if (!shouldPreserveTaskPanelForSession(sessionId, '/workspace/chat')) setSidePaneOpen(false);
    // 让位回开标记同边界作废：副屏已 closePanelForce 清 tabs，回开会被 autoCloseWhenEmpty 空弹。
    sidePaneMutex.clearSidePaneExclusion();
    setSessionEpoch((epoch) => epoch + 1);
  }
  useEffect(() => {
    markTaskNavigationRouteHydrated(sessionId, '/workspace/chat');
  }, [sessionId]);

  // 副屏判空：无内容也不可重建 Task tab 时才禁用 Monitor；已打开的 TaskPanel 必须保持可切。
  const sidePaneDisabled = useMemo(
    () => !sidePaneEnabled || (!sidePaneOpen && !sessionHasSidePaneContent(messages) && !taskTab),
    [messages, sidePaneEnabled, sidePaneOpen, taskTab],
  );

  const toggleSidePane = useCallback(() => {
    if (!sidePaneEnabled) return;
    setSidePaneOpen((current) => {
      const next = !current;
      const panel = panelRef.current;
      if (!panel) return current; // 面板容器未挂载（只读态/舞台切换中）：不翻镜像。
      if (!next) {
        panel.closePanel();
        return false;
      }
      // 打开：按消息标记幂等重建 tabs，不再裸 openPanel——空 tabs 会被 autoCloseWhenEmpty
      // 立即弹回（「打开一瞬又关闭」）；重建成功即面板已展开，镜像同步为 true。
      return reopenSidePaneTabsFromMessages(messages, panel, taskTab);
    });
  }, [panelRef, messages, sidePaneEnabled, taskTab]);

  const favorites = useMemo<ChatHeaderFavoritesModel>(() => {
    const favorite = session?.favorite === true;
    const pending =
      session && options.botId ? options.favoritePending(options.botId, options.section, session.sessionId) : false;
    return {
      favorite,
      disabled: !session || session.favorite === undefined || !options.botId || pending,
      onToggle: () => {
        if (!session || !options.botId) return;
        void options.toggleFavorite(options.botId, options.section, session.sessionId);
      },
    };
  }, [session, options]);

  const historySearch = useSessionHistorySearchModel(messages, sessionEpoch);

  const locateMessage = useCallback((messageId: string) => {
    setOpenPanel(null);
    setHighlightMessageId(messageId);
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    highlightTimer.current = setTimeout(() => setHighlightMessageId(null), HIGHLIGHT_TIMEOUT_MS);
  }, []);

  return {
    openPanel,
    togglePanel,
    closePanel,
    dismissSidePaneForPanels: sidePaneMutex.dismissSidePaneForPanels,
    resetAll,
    favorites,
    sidePaneOpen,
    sidePaneDisabled,
    toggleSidePane,
    onSidePaneIsOpenChange: sidePaneMutex.onSidePaneIsOpenChange,
    historySearch,
    highlightMessageId,
    locateMessage,
  };
}
