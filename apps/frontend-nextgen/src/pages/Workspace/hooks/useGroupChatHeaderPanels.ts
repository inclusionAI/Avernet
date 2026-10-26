/**
 * useGroupChatHeaderPanels —— 协作群头部六图标域（收藏/历史消息/副屏）的开合编排（群侧孪生）。
 *
 * 与 chat 侧 useChatHeaderPanels 的差异：
 * - 右缘面板互斥由 GroupWorkspaceArea 的单一 activePanel 统一（会话管理/历史消息/资源管理），
 *   本 Hook 不再持有面板开关态，只承载三块纯本地域：
 *   收藏模型（SessionView.favorite）、历史搜索（前端过滤，Service 同源）、副屏镜像开合。
 * - 群侧 GroupChatPane 在会话切换时 closePanelForce()，故副屏开合态必须随切换复位
 *   （chat 孪生已在同边界补齐复位）；sidePaneOpen 经 ChatLayoutPanelSlot 承接 SDK
 *   onIsOpenChange 保持真实开合态（含渲染器「挂载即 openTab」的默认展开），
 *   toggle 仅乐观翻转、随后由真实态事件纠偏；副屏打开路径共享 chat 侧
 *   reopenSidePaneTabsFromMessages（按消息标记幂等重建，防 autoCloseWhenEmpty 弹回），
 *   图标判空共享 sessionHasSidePaneContent。
 * 历史搜索状态收口本 Hook（useMemo 前端过滤，Service 双源）。
 */
import type { SessionView } from '@/domain/collaboration';
import type { UseSessionHistorySearchModel } from '@/pages/Workspace/Chat/hooks/useChatHeaderPanels';
import { reopenSidePaneTabsFromMessages, sessionHasSidePaneContent } from '@/pages/Workspace/Chat/hooks/useChatHeaderPanels';
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
  /** 与消息区渲染同源的已载入消息（过滤后口径，保证每条搜索结果可定位）。 */
  messages: ChatMessage[];
  /** 副屏 handle（与 ChatLayout.Panel panelRef 同源）。 */
  panelRef: React.RefObject<PanelHandle | null>;
  /** 收藏切换出口（useGroupSessions.toggleFavorite；群侧无 pending 模型，不发明）。 */
  toggleFavorite: (sessionId: string) => Promise<void>;
}

export interface UseGroupChatHeaderPanelsResult {
  favorites: GroupChatFavoritesModel;
  historySearch: UseSessionHistorySearchModel;
  /** 副屏真实开合态（ChatLayoutPanelSlot onIsOpenChange 同步；toggle 乐观翻转其后纠偏）。 */
  sidePaneOpen: boolean;
  /** 会话无副屏内容（消息无 <AixUI type="panel">）时 Monitor 图标禁用，toggle 空转。 */
  sidePaneDisabled: boolean;
  toggleSidePane: () => void;
  /** 真实开合同步入口（接线至 ChatLayoutPanelSlot onOpenStateChange）。 */
  onSidePaneIsOpenChange: (open: boolean) => void;
  /** 点击搜索结果后待定位高亮的 messageId（3s 后自动清除）。 */
  highlightMessageId: string | null;
  locateMessage: (messageId: string) => void;
}

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

export function useGroupChatHeaderPanels(options: UseGroupChatHeaderPanelsOptions): UseGroupChatHeaderPanelsResult {
  const { session, messages, panelRef } = options;

  // 副屏开合态：默认 false，经 ChatLayoutPanelSlot 承接 SDK onIsOpenChange 的真实态事件同步
  // （含渲染器「挂载即 openTab」的默认展开），toggle 仅乐观翻转、随后由真实态事件纠偏。
  const [sidePaneOpen, setSidePaneOpen] = useState(false);
  const onSidePaneIsOpenChange = useCallback((open: boolean) => setSidePaneOpen(open), []);
  const [highlightMessageId, setHighlightMessageId] = useState<string | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 会话切换即复位（面板内容按当前会话呈现；副屏被 closePanelForce 强关，镜像必须同步回 false）。
  const sessionId = session?.sessionId ?? null;
  const lastSessionIdRef = useRef<string | null>(sessionId);
  const [sessionEpoch, setSessionEpoch] = useState(0);
  if (lastSessionIdRef.current !== sessionId) {
    lastSessionIdRef.current = sessionId;
    if (!shouldPreserveTaskPanelForSession(sessionId, '/workspace/collaboration')) setSidePaneOpen(false);
    setHighlightMessageId(null);
    setSessionEpoch((epoch) => epoch + 1);
  }
  useEffect(() => {
    markTaskNavigationRouteHydrated(sessionId, '/workspace/collaboration');
  }, [sessionId]);

  // 副屏判空：无内容时禁用 Monitor（openPanel 只会被 SDK autoCloseWhenEmpty 弹回）。
  const sidePaneDisabled = useMemo(() => !sessionHasSidePaneContent(messages), [messages]);

  const toggleSidePane = useCallback(() => {
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
      return reopenSidePaneTabsFromMessages(messages, panel);
    });
  }, [panelRef, messages]);

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
    sidePaneOpen,
    sidePaneDisabled,
    toggleSidePane,
    onSidePaneIsOpenChange,
    highlightMessageId,
    locateMessage,
  };
}
