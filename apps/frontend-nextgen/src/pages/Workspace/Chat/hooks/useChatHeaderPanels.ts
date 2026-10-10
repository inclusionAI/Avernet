/**
 * useChatHeaderPanels —— 对话页头部五图标域的开合编排（chat-header-panels）。
 *
 * 五图标（dmore 实测语义）：
 * - 收藏会话：动作开关，复用 useConversationFavorites.toggleFavorite；
 * - 会话管理 / 历史消息：右缘面板互斥开合（新面板打开关闭旧面板）；
 * - 资源管理：收敛既有会话文件副屏（openFileDrawer / closeFileDrawer，同一容器）；
 * - 副屏：panelRef.openPanel/closePanel toggle（taskPanel 副屏链路沿用，无 isOpen
 *   查询能力，以本地镜像态近似）。
 * 历史搜索状态收口本 Hook（useMemo 前端过滤，Service 双源）。
 */
import type { ConversationBotSection } from '@/domain/conversation';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import type {
  SessionMessageSearchQuery,
  SessionMessageSearchResult,
} from '@/services/workspace/conversationSearchService';
import { searchConversationMessages } from '@/services/workspace/conversationSearchService';
import type { ChatMessage, PanelHandle } from '@tc-chat/core';
import { useCallback, useMemo, useRef, useState } from 'react';

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
  sidePaneOpen: boolean;
  toggleSidePane: () => void;
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
  // 副屏 toggle 的本地镜像态（PanelHandle 无 isOpen 查询）。
  const [sidePaneOpen, setSidePaneOpen] = useState(false);
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
    setSessionEpoch((epoch) => epoch + 1);
  }

  const toggleSidePane = useCallback(() => {
    setSidePaneOpen((current) => {
      const next = !current;
      if (next) panelRef.current?.openPanel();
      else panelRef.current?.closePanel();
      return next;
    });
  }, [panelRef]);

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
    toggleSidePane,
    historySearch,
    highlightMessageId,
    locateMessage,
  };
}
