/**
 * useGroupChatHeaderPanels —— 协作群头部六图标域（收藏/历史消息/副屏）的开合编排（群侧孪生）。
 *
 * 与 chat 侧 useChatHeaderPanels 的差异：
 * - 右缘面板互斥由 GroupWorkspaceArea 的单一 activePanel 统一（会话管理/历史消息/资源管理），
 *   本 Hook 不再持有面板开关态，只承载纯本地域：收藏模型（SessionView.favorite）、
 *   历史搜索（前端过滤，Service 同源）、副屏镜像开合。
 * - 群侧 GroupChatPane 在会话切换时 closePanelForce()，故副屏开合态必须随切换复位
 *   （chat 孪生已在同边界补齐复位）；sidePaneOpen 经 ChatLayoutPanelSlot 承接 SDK
 *   onIsOpenChange 保持真实开合态（含渲染器「挂载即 openTab」的默认展开），
 *   toggle 仅乐观翻转、随后由真实态事件纠偏；副屏打开路径共享 chat 侧
 *   reopenSidePaneTabsFromMessages（按消息标记幂等重建，防 autoCloseWhenEmpty 弹回），
 *   图标判空共享 sessionHasSidePaneContent。
 * - 任务副屏 bootstrap 经 taskPanelBootstrap 槽位装配（collab 形态注册 null：不重放任务面板
 *   页签、不拉任务栈，spec §5）；chat 孪生仍直接 import 真实实现，本 hook 的静态闭包
 *   因此不含任务引擎链。
 * - TaskPanel 重开快照（useSessionTaskPanelTab）同样经 sessionTaskPanelTab 槽位装配
 *   （collab 形态 null → 恒无快照，Monitor 判空退化为消息标记判定）。
 * - 右缘域面板 × 副屏互斥由共享 useSidePanePanelMutex 编排，保留发布分支的让位/恢复行为。
 * 历史搜索状态收口本 Hook（useMemo 前端过滤，Service 双源）。
 */
import type { SessionView } from '@/domain/collaboration';
import { getExtensionSlots } from '@/extensions/slots';
import type { AppExtensionSlots } from '@/extensions/contract';
import { reopenSidePaneTabsFromMessages, sessionHasSidePaneContent } from '@/pages/Workspace/Chat/hooks/sidePaneContent';
// 历史搜索模型改经 conversationSearchService 的纯模型 re-export 供给（collab-form Task 17 源切片类型完备）：
// chat 侧 header hook 属 pages/Workspace/Chat 裁剪面，本保留链（collab）不得 type-only 依赖它。
import type { UseSessionHistorySearchModel } from '@/services/workspace/conversationSearchService';
import { useSidePanePanelMutex } from '@/pages/Workspace/hooks/useSidePanePanelMutex';
import {
  markTaskNavigationRouteHydrated,
  shouldPreserveTaskPanelForSession,
} from '@/services/tasks/taskConversationNavigation';
import { searchConversationMessages } from '@/services/workspace/conversationSearchService';
import type { ChatMessage, PanelHandle } from '@tc-chat/core';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export interface GroupChatFavoritesModel {
  favorite: boolean;
  disabled: boolean;
  onToggle: () => void;
}

export interface UseGroupChatHeaderPanelsOptions {
  session: SessionView | null;
  /** 当前登录用户任务列表查询口径。 */
  userId: string | null;
  /** 与消息区渲染同源的已载入消息（过滤后口径，保证每条搜索结果可定位）。 */
  messages: ChatMessage[];
  /** 副屏 handle（与 ChatLayout.Panel panelRef 同源）。 */
  panelRef: React.RefObject<PanelHandle | null>;
  /** 收藏切换出口（useGroupSessions.toggleFavorite；群侧无 pending 模型，不发明）。 */
  toggleFavorite: (sessionId: string) => Promise<void>;
  /** 右缘域面板（GroupWorkspaceArea activePanel）是否有面板在开——右缘面板 × 副屏互斥。 */
  panelsOpen?: boolean;
  /** 副屏展开时收右缘域面板（setActivePanel('none')）。 */
  closePanels?: () => void;
}

export interface UseGroupChatHeaderPanelsResult {
  favorites: GroupChatFavoritesModel;
  historySearch: UseSessionHistorySearchModel;
  /** 右缘域面板即将展开前的副屏让位：收起副屏并记录回开标记，面板全收后自动回开。 */
  dismissSidePaneForPanels: () => void;
  /** 副屏真实开合态（ChatLayoutPanelSlot onIsOpenChange 同步；toggle 乐观翻转其后纠偏）。 */
  sidePaneOpen: boolean;
  /** 会话无副屏内容（消息无 <AixUI type="panel">）时 Monitor 图标禁用，toggle 空转。 */
  sidePaneDisabled: boolean;
  toggleSidePane: () => void;
  /** 真实开合同步入口（接线至 ChatLayoutPanelSlot onOpenStateChange：镜像 + 互斥收面板）。 */
  onSidePaneIsOpenChange: (open: boolean) => void;
  /** 点击搜索结果后待定位高亮的 messageId（3s 后自动清除）。 */
  highlightMessageId: string | null;
  locateMessage: (messageId: string) => void;
}

/** 互斥未装配（panelsOpen/closePanels 缺省）时的占位收面板，不得影响回调稳定身份。 */
const noopPanelsClose = () => undefined;

/** 历史消息面板的搜索/筛选/结果模型（本地源即时过滤；与 chat 侧同构）。 */
function useSessionHistorySearchModel(messages: ChatMessage[], sessionEpoch: number): UseSessionHistorySearchModel {
  const [keyword, setKeyword] = useState('');
  const [sender, setSender] = useState<UseSessionHistorySearchModel['query']['sender']>(null);
  const [todayOnly, setTodayOnly] = useState(false);

  // 会话切换复位筛选（sessionEpoch 变化即重置）。
  const lastEpochRef = useRef(sessionEpoch);
  if (lastEpochRef.current !== sessionEpoch) {
    lastEpochRef.current = sessionEpoch;
    setKeyword('');
    setSender(null);
    setTodayOnly(false);
  }

  const outcome = useMemo(
    () => searchConversationMessages(messages, { keyword, sender, todayOnly }),
    [messages, keyword, sender, todayOnly],
  );
  return {
    query: { keyword, sender, todayOnly },
    setKeyword,
    setSender,
    setTodayOnly,
    source: outcome.source,
    results: outcome.items,
  };
}

const HIGHLIGHT_TIMEOUT_MS = 3000;

// 任务副屏 bootstrap 槽位（collab 形态注册 null）：hook 身份按构建恒定，不违反 hooks 规则。
// collab 下不重放任务面板页签、不发起 services/myTask 拉取（spec §5：任务引擎不进本形态运行时面）。
// 模块图不变量（见 src/extensions/slots.ts）：本文件解析的是「求值时刻」的注册表，不得被
// src/extensions 装配图静态引达（否则会捕获全 null 默认槽位，静默丢弃 Open 任务副屏 bootstrap）。
const idleTaskRecovery = { status: 'idle' as const, continueRecovery: async () => {} };
const useNullTaskPanelBootstrap: NonNullable<AppExtensionSlots['taskPanelBootstrap']> = () => idleTaskRecovery;
const useResolvedTaskPanelBootstrap = getExtensionSlots().taskPanelBootstrap ?? useNullTaskPanelBootstrap;
const useNullSessionTaskPanelTab: NonNullable<AppExtensionSlots['sessionTaskPanelTab']> = () => null;
const useResolvedSessionTaskPanelTab = getExtensionSlots().sessionTaskPanelTab ?? useNullSessionTaskPanelTab;

export function useGroupChatHeaderPanels(options: UseGroupChatHeaderPanelsOptions): UseGroupChatHeaderPanelsResult {
  const { session, messages, panelRef } = options;
  const sessionId = session?.sessionId ?? null;

  const taskRecovery = useResolvedTaskPanelBootstrap({ sessionId, userId: options.userId, panelRef });

  // 副屏开合态：默认 false，经 ChatLayoutPanelSlot 承接 SDK onIsOpenChange 的真实态事件同步
  // （含渲染器「挂载即 openTab」的默认展开），toggle 仅乐观翻转、随后由真实态事件纠偏。
  const taskTab = useResolvedSessionTaskPanelTab(sessionId);

  const [sidePaneOpen, setSidePaneOpen] = useState(false);
  // 右缘域面板 × 副屏互斥：群侧面板与 chat 同为右缘 aside，副屏（AppShell 悬浮层）展开后
  // 整条盖住面板让位区（chat 侧已修的同款掩盖几何）——复用同一互斥 Hook，装配参数由
  // GroupWorkspaceArea 注入（activePanel 单一来源 → panelsOpen / closePanels）。
  const sidePaneMutex = useSidePanePanelMutex({
    panelRef,
    sidePaneOpen,
    panelsOpen: options.panelsOpen ?? false,
    closePanels: options.closePanels ?? noopPanelsClose,
    onMirrorChange: setSidePaneOpen,
  });
  const [highlightMessageId, setHighlightMessageId] = useState<string | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 会话切换即复位（面板内容按当前会话呈现；副屏被 closePanelForce 强关，镜像必须同步回 false）。
  const lastSessionIdRef = useRef<string | null>(sessionId);
  const [sessionEpoch, setSessionEpoch] = useState(0);
  if (lastSessionIdRef.current !== sessionId) {
    lastSessionIdRef.current = sessionId;
    if (!shouldPreserveTaskPanelForSession(sessionId, '/workspace/collaboration')) setSidePaneOpen(false);
    // 让位回开标记同边界作废：副屏已 closePanelForce 清 tabs，回开会被 autoCloseWhenEmpty 空弹。
    sidePaneMutex.clearSidePaneExclusion();
    setHighlightMessageId(null);
    setSessionEpoch((epoch) => epoch + 1);
  }
  useEffect(() => {
    markTaskNavigationRouteHydrated(sessionId, '/workspace/collaboration');
  }, [sessionId]);

  // 副屏判空：无内容也不可重建 Task tab 时才禁用 Monitor；已打开的 TaskPanel 必须保持可切。
  const sidePaneDisabled = useMemo(
    () => !sidePaneOpen && !sessionHasSidePaneContent(messages) && !taskTab && !['incomplete', 'error'].includes(taskRecovery.status),
    [messages, sidePaneOpen, taskTab, taskRecovery.status],
  );

  const toggleSidePane = useCallback(() => {
    if (['incomplete', 'error'].includes(taskRecovery.status)) { void taskRecovery.continueRecovery(); return; }
    setSidePaneOpen((current) => {
      const next = !current;
      const panel = panelRef.current;
      if (!panel) return current; // 面板容器未挂载（未选群/舞台切换中）：不翻镜像。
      if (!next) {
        panel.closePanel();
        return false;
      }
      // 打开：按消息标记幂等重建 tabs，不再裸 openPanel——空 tabs 会被 autoCloseWhenEmpty
      // 立即弹回（「打开一瞬又关闭」，切会话与渲染器 openTab 的时序竞态所致）。
      return reopenSidePaneTabsFromMessages(messages, panel, taskTab);
    });
  }, [panelRef, messages, taskTab, taskRecovery.status, taskRecovery.continueRecovery]);

  const favorites = useMemo<GroupChatFavoritesModel>(() => {
    const favorite = session?.favorite === true;
    return {
      favorite,
      disabled: !session,
      onToggle: () => {
        if (!session) return;
        void options.toggleFavorite(session.sessionId);
      },
    };
  }, [session, options]);

  const historySearch = useSessionHistorySearchModel(messages, sessionEpoch);

  const locateMessage = useCallback((messageId: string) => {
    setHighlightMessageId(messageId);
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    highlightTimer.current = setTimeout(() => setHighlightMessageId(null), HIGHLIGHT_TIMEOUT_MS);
  }, []);

  return {
    favorites,
    historySearch,
    dismissSidePaneForPanels: sidePaneMutex.dismissSidePaneForPanels,
    sidePaneOpen,
    sidePaneDisabled,
    toggleSidePane,
    onSidePaneIsOpenChange: sidePaneMutex.onSidePaneIsOpenChange,
    highlightMessageId,
    locateMessage,
  };
}
