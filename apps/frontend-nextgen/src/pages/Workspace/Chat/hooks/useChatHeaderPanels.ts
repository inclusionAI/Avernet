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
 *   图标禁用，防 autoCloseWhenEmpty 弹回）。
 * 历史搜索状态收口本 Hook（useMemo 前端过滤，Service 双源）。
 */
import type { ConversationBotSection } from '@/domain/conversation';
import {
  markTaskNavigationRouteHydrated,
  shouldPreserveTaskPanelForSession,
} from '@/services/tasks/taskConversationNavigation';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import type {
  SessionMessageSearchQuery,
  SessionMessageSearchResult,
} from '@/services/workspace/conversationSearchService';
import { searchConversationMessages } from '@/services/workspace/conversationSearchService';
import type { ChatMessage, PanelHandle } from '@tc-chat/core';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { reopenSidePaneTabsFromMessages, sessionHasSidePaneContent } from './sidePaneContent';

// 副屏内容判定与幂等重建已下沉至同目录 sidePaneContent.ts（工程红线：Hook 单文件 ≤250 行）；
// 保留再导出兼容既有导入面（useGroupChatHeaderPanels / 测试用例）。
export { reopenSidePaneTabsFromMessages, sessionHasSidePaneContent } from './sidePaneContent';

/** 头部右侧承载的右缘面板类型（资源面板走既有文件副屏，不在此枚举内）。 */
export type ChatHeaderPanelKind = 'detail' | 'history';

export interface ChatHeaderFavoritesModel {
  favorite: boolean;
  disabled: boolean;
  onToggle: () => void;
}

export interface UseChatHeaderPanelsOptions {
  session: BotChatSessionView | null;
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
}

export interface UseChatHeaderPanelsResult {
  /** 当前互斥开合中的面板；资源面板开合用 fileDrawerOpen 表达。 */
  openPanel: ChatHeaderPanelKind | null;
  togglePanel: (kind: ChatHeaderPanelKind) => void;
  closePanel: () => void;
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

export interface UseSessionHistorySearchModel {
  query: SessionMessageSearchQuery;
  setKeyword: (keyword: string) => void;
  setSender: (sender: SessionMessageSearchQuery['sender']) => void;
  setTodayOnly: (todayOnly: boolean) => void;
  /** 本地降级态指示：面板据此明示「仅已载入消息」。 */
  source: 'local';
  results: SessionMessageSearchResult[];
}

/** 历史消息面板的搜索/筛选/结果模型（本地源即时过滤）。 */
function useSessionHistorySearchModel(messages: ChatMessage[], sessionEpoch: number): UseSessionHistorySearchModel {
  const [keyword, setKeyword] = useState('');
  const [sender, setSender] = useState<SessionMessageSearchQuery['sender']>(null);
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

export function useChatHeaderPanels(options: UseChatHeaderPanelsOptions): UseChatHeaderPanelsResult {
  const { session, messages, panelRef } = options;

  const [openPanel, setOpenPanel] = useState<ChatHeaderPanelKind | null>(null);
  // 副屏开合态：默认 false，经 ChatLayoutPanelSlot 承接 SDK onIsOpenChange 的真实态事件同步
  // （含渲染器「挂载即 openTab」的默认展开），toggle 仅乐观翻转、随后由真实态事件纠偏。
  const [sidePaneOpen, setSidePaneOpen] = useState(false);
  const onSidePaneIsOpenChange = useCallback((open: boolean) => setSidePaneOpen(open), []);
  const [highlightMessageId, setHighlightMessageId] = useState<string | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 互斥开合：再次点击同一面板关闭；资源文件副屏与本组右缘面板同属头部域，一并互斥。
  const togglePanel = useCallback(
    (kind: ChatHeaderPanelKind) => {
      setOpenPanel((current) => {
        const next = current === kind ? null : kind;
        if (next !== null) options.closeFileDrawer();
        return next;
      });
    },
    [options],
  );
  const closePanel = useCallback(() => setOpenPanel(null), []);
  const resetAll = useCallback(() => {
    setOpenPanel(null);
    options.closeFileDrawer();
  }, [options]);

  // 会话切换即复位开合态（面板内容按当前会话呈现）。
  const sessionId = session?.sessionId ?? null;
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
    setSessionEpoch((epoch) => epoch + 1);
  }
  useEffect(() => {
    markTaskNavigationRouteHydrated(sessionId, '/workspace/chat');
  }, [sessionId]);

  // 副屏判空：无内容时禁用 Monitor（openPanel 只会被 autoCloseWhenEmpty 弹回）。
  const sidePaneDisabled = useMemo(() => !sessionHasSidePaneContent(messages), [messages]);

  const toggleSidePane = useCallback(() => {
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
      return reopenSidePaneTabsFromMessages(messages, panel);
    });
  }, [panelRef, messages]);

  const favorites = useMemo<ChatHeaderFavoritesModel>(() => {
    const favorite = session?.favorite === true;
    const pending =
      session && options.botId ? options.favoritePending(options.botId, options.section, session.sessionId) : false;
    return {
      favorite,
      disabled: !session || !options.botId || pending,
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
    resetAll,
    favorites,
    sidePaneOpen,
    sidePaneDisabled,
    toggleSidePane,
    onSidePaneIsOpenChange,
    historySearch,
    highlightMessageId,
    locateMessage,
  };
}
