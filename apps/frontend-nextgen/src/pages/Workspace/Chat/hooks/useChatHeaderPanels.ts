/**
 * useChatHeaderPanels —— 对话页头部五图标域的开合编排（chat-header-panels）。
 *
 * 五图标（dmore 实测语义）：
 * - 收藏会话：动作开关，复用 useConversationFavorites.toggleFavorite；
 * - 会话管理 / 历史消息：右缘面板互斥开合（新面板打开关闭旧面板）；
 * - 资源管理：收敛既有会话文件副屏（openFileDrawer / closeFileDrawer，同一容器）；
 * - 副屏：closePanel 关闭；打开走 reopenSidePaneTabsFromMessages 按消息标记幂等重建
 *   （PanelHandle 无 isOpen 查询能力，以本地镜像态近似；会话切换复位镜像——useBotChat
 *   同边界 closePanelForce；无 <AixUI type="panel"> 内容时图标禁用，防 autoCloseWhenEmpty 弹回）。
 * 历史搜索状态收口本 Hook（useMemo 前端过滤，Service 双源）。
 */
import type { ConversationBotSection } from '@/domain/conversation';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import type {
  SessionMessageSearchQuery,
  SessionMessageSearchResult,
} from '@/services/workspace/conversationSearchService';
import { searchConversationMessages } from '@/services/workspace/conversationSearchService';
import type { ChatMessage, PanelHandle, PanelTab } from '@tc-chat/core';
import { openBusinessPanel } from '@tc-chat/ui/es/MarkdownRender/plugins/components/businessUtils';
import {
  buildBusinessData,
  extractTagAttr,
  resolveAixUi,
  type AixUiRawAttrs,
} from '@tc-chat/ui/es/utils/aixTags';
import { useCallback, useMemo, useRef, useState } from 'react';

/** 头部右侧承载的右缘面板类型（资源面板走既有文件副屏，不在此枚举内）。 */
export type ChatHeaderPanelKind = 'detail' | 'history';

/**
 * AixUI 副屏标记：显式 type=panel（双引号/单引号/无引号均命中，引擎与 BCS 输出引号风格不一），
 * 或无显式 type 时的旧版 panel= 属性（对齐 @tc-chat/ui resolveAixUi 的位置轴：
 * panel-component/panel-card）。[^>] 含换行，开标签跨行属性同样可命中。
 */
const AIX_UI_PANEL_TAG_RE = /<AixUI[^>]*\stype=["']?panel(?=["'\s>])|<AixUI[^>]*\spanel=["']?/;

/**
 * 会话已载入消息中是否有副屏内容。
 * PanelHandle 无 tabs 查询；空 tabs 的 openPanel 会被 SDK autoCloseWhenEmpty 立即弹回
 * （面板闪一下即收回，观感即「点了没反应」），故 Monitor 开启动作以此为判空依据——
 * 消息含副屏标记时其渲染器挂载即 openTab，tabs 必然存在，openPanel 不会弹回。
 */
export function sessionHasSidePaneContent(messages: ChatMessage[]): boolean {
  return messages.some((message) => typeof message.content === 'string' && AIX_UI_PANEL_TAG_RE.test(message.content));
}

/** AixUiComponent 的 rawAttrs 属性白名单。 */
const AIX_UI_ATTR_NAMES = ['type', 'cardId', 'component', 'params', 'payload', 'tab', 'panel', 'cdn', 'entry'] as const;

/** SDK panel-card 模式的 tab.type 常量（AixUiComponent CARD_PANEL_KEY）。 */
const CARD_PANEL_KEY = 'aix-card-panel';

/**
 * 按消息里的副屏声明幂等重建副屏 tabs（chat/群两页 Monitor「打开」共用）。
 *
 * 背景：切会话 closePanelForce 与渲染器「挂载即 openTab」存在时序竞态——父组件 effect
 * 晚于子组件 effect 执行，刚还原的 tab 会被抹掉且渲染器不再重试；此时裸 openPanel()
 * 只会被 SDK autoCloseWhenEmpty 立即弹回，表现为「打开一瞬又关闭」。
 *
 * 解法：Monitor 打开改为走消息标记 openTab 幂等重建——openTab 同 id 静默合并
 * （不重挂内容、不抢焦点），tab 缺失时原样重建，且 openTab 自动展开面板、
 * 重建后 tab 必在，autoCloseWhenEmpty 不会再弹回。返回是否开出至少一个 tab。
 */
export function reopenSidePaneTabsFromMessages(messages: ChatMessage[], panel: PanelHandle): boolean {
  let openedAny = false;
  for (const message of messages) {
    const content = message.content;
    if (typeof content !== 'string' || !/<aixui/i.test(content)) continue;
    const openTagRe = /<AixUI\b([^>]*)>/gi;
    let match: RegExpExecArray | null;
    while ((match = openTagRe.exec(content)) !== null) {
      const rawAttrsText = match[1] ?? '';
      // 自闭合 <AixUI ... />：无标签体（『/』是属性串最后一个非空白字符）。
      const closingSlashIdx = rawAttrsText.trimEnd().endsWith('/') ? rawAttrsText.length - 1 : -1;
      const selfClosing = closingSlashIdx !== -1;
      const attrsText = selfClosing ? rawAttrsText.slice(0, closingSlashIdx) : rawAttrsText;
      const bodyStart = match.index + match[0].length;
      let body = '';
      if (!selfClosing) {
        // 成对标签截 body（MarkdownRender lexer 走 lexer.blockTokens，这里简化为取直接文本）；未闭合为流式中态，跳过。
        const closeIdx = content.toLowerCase().indexOf('</aixui>', bodyStart);
        if (closeIdx === -1) continue;
        body = content.slice(bodyStart, closeIdx).trim();
      }
      const rawAttrs: AixUiRawAttrs = {};
      for (const name of AIX_UI_ATTR_NAMES) {
        const value = extractTagAttr(attrsText, name);
        if (value !== undefined) rawAttrs[name] = value;
      }
      const resolved = resolveAixUi(rawAttrs);
      if (resolved.mode === 'panel-card' && resolved.cardId) {
        // 与 PanelCardTrigger 同款 payload：{cardId, params, data(标签体 JSON)}。
        const data = buildBusinessData(resolved.params, resolved.payload) as Record<string, unknown>;
        if (body) {
          try {
            data.content = JSON.parse(body);
          } catch {
            data.content = body;
          }
        }
        const payload = { cardId: resolved.cardId, params: resolved.params ?? {}, data: data.content };
        panel.openTab({
          id: resolved.tab.id || resolved.cardId,
          type: CARD_PANEL_KEY,
          title: resolved.tab.title ?? resolved.cardId,
          closable: resolved.tab.closable ?? false,
          ...(Object.prototype.hasOwnProperty.call(resolved.tab, 'defaultWidth')
            ? // TabConfig.defaultWidth 为 unknown（运行时由 ChatLayout.openTab 归一化），与 PanelCardTrigger 同形透传。
              { defaultWidth: resolved.tab.defaultWidth as PanelTab['defaultWidth'] }
            : {}),
          params: payload,
          payload,
        });
        openedAny = true;
      } else if (resolved.mode === 'panel-component' && resolved.component) {
        // 与 PanelComponentTrigger 同款：命令式 openBusinessPanel（tab.id 缺省取 component）。
        openBusinessPanel(panel, {
          component: resolved.component,
          cdn: resolved.cdn,
          entry: resolved.entry,
          params: resolved.params,
          payload: resolved.payload,
          tab: resolved.tab,
          children: body || undefined,
        });
        openedAny = true;
      }
    }
  }
  return openedAny;
}

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
  /** 会话无副屏内容（消息无 <AixUI type="panel">）时 Monitor 图标禁用，toggle 空转。 */
  sidePaneDisabled: boolean;
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
    // 副屏镜像同步复位：useBotChat 在同一边界 closePanelForce 强关面板并清空 tabs
    // （与群侧孪生 useGroupChatHeaderPanels 一致，防 Monitor 激活态与实际开合脱钩）。
    setSidePaneOpen(false);
    setSessionEpoch((epoch) => epoch + 1);
  }

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
    historySearch,
    highlightMessageId,
    locateMessage,
  };
}
