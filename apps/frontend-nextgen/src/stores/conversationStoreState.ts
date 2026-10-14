// Conversation Store 的状态类型与初始状态(纯同步 setter 契约)。
// 设计依据:docs/specs/2026-09-24-workspace-conversation-navigation-refactor.md §7。
// Sidebar prop 的 ConversationState 语义以本文件导出的 ConversationState 为准。
import type {
  ConversationBotSection,
  ConversationOrigin,
  ConversationSessionListState,
  ConversationSessionScope,
  ConversationUserView,
  ManagedBotConversationView,
} from '@/domain/conversation';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';

// 缓存结构类型从 Domain 收口再导出,消费方(页面/组件/Hook)统一从 Store 取面向 UI 的类型。
export type {
  ConversationBotSection,
  ConversationOrigin,
  ConversationSessionListState,
  ConversationSessionScope,
  ManagedBotConversationView,
} from '@/domain/conversation';

export interface ConversationState {
  /** UI 同步状态:管理/团队/好友 Bot 共用，同一时刻至多展开一个 Bot。 */
  expandedBotIds: Record<string, true>;
  /** 按管理 Bot 记忆的发起归属;页面会话内记忆,不持久化。 */
  originByManagedBotId: Record<string, ConversationOrigin>;
  /** 按管理 Bot 记忆的 mine 会话范围;切到 others 不清除,切回 mine 恢复。 */
  scopeByManagedBotId: Record<string, ConversationSessionScope>;
  /** 当前生效的读取范围;origin=others 强制 all,其余等于记忆范围(缺省 all)。 */
  effectiveScopeByManagedBotId: Record<string, ConversationSessionScope>;
  /** 管理 Bot others 视角下,按 Bot 记忆的好友用户展开(key 为纯用户 ID,不带 human_)。 */
  expandedFriendUserIdsByBotId: Record<string, Record<string, true>>;
  /** 侧栏 Bot 分组(我的/好友)收起记忆,key 为分组 ID(managed/friend);缺省展开。 */
  collapsedBotGroups: Record<string, true>;

  /** 按 Bot 记忆的 sessionKey 搜索条件(trim 后保存;空条件不落记录);页面会话内记忆,重置用户状态时清空。 */
  sessionKeyByBotId: Record<string, string>;
  /** 按好友 Bot 记忆的会话范围;缺省 all,与 scopeByManagedBotId 同语义。 */
  scopeByFriendBotId: Record<string, ConversationSessionScope>;
  /** 每次 Bot 过滤条件实际变化 +1;供 Hook 组装请求代次,阻止旧响应(含 A→B→A)写回。 */
  sessionFilterRevisionByBotId: Record<string, number>;

  /** 主舞台当前选中(全 Store 单选);URL 投影由 Hook 写回 route。 */
  selectedBotId: string | null;
  selectedSection: ConversationBotSection | null;
  selectedOrigin: ConversationOrigin;
  selectedScope: ConversationSessionScope;
  selectedFriendUserId: string | null;
  selectedSessionId: string | null;

  /** 远程只读缓存:管理 Bot 会话/好友目录/好友分组状态,由 Hook 经 Service 写入。 */
  sessionsByBotId: Record<string, ManagedBotConversationView>;
  /** 远程只读缓存:好友 Bot 会话列表,由 Hook 经 Service 写入。 */
  friendBotSessionsByBotId: Record<string, ConversationSessionListState>;

  /** 已打开主会话的保留快照:sessionKey/好友范围过滤把该会话移出列表时仍驱动主舞台
   *  (spec: 搜索只改变侧栏列表,不自动关闭已打开的主会话)。由行选择/自动首选/
   *  创建/只读选择写入;仅当快照身份与当前选中完全一致时生效。 */
  selectedSessionSnapshot: SelectedSessionSnapshot | null;

  setExpandedBot(botId: string, expanded: boolean): void;
  setManagedBotOrigin(botId: string, origin: ConversationOrigin): void;
  setManagedBotScope(botId: string, scope: ConversationSessionScope): void;
  setSessionKeyFilter(botId: string, sessionKey: string): void;
  setFriendBotScope(botId: string, scope: ConversationSessionScope): void;
  setExpandedFriend(botId: string, friendUserId: string, expanded: boolean): void;
  setBotGroupCollapsed(groupId: string, collapsed: boolean): void;
  selectConversation(input: {
    botId: string | null;
    section: ConversationBotSection | null;
    origin: ConversationOrigin;
    scope: ConversationSessionScope;
    friendUserId: string | null;
    sessionId: string | null;
  }): void;
  setManagedBotCache(botId: string, value: ManagedBotConversationView): void;
  setFriendBotSessions(botId: string, value: ConversationSessionListState): void;
  setSelectedSessionSnapshot(value: SelectedSessionSnapshot): void;
  reset(): void;
}

/** 选中会话的保留快照载荷:身份字段与 selectConversation 的选中输入一致。
 *  session.sessionId 即被选中的会话;others 分组好友资料供只读舞台使用。 */
export interface SelectedSessionSnapshot {
  section: ConversationBotSection;
  botId: string;
  origin: ConversationOrigin;
  scope: ConversationSessionScope;
  friendUserId: string | null;
  session: BotChatSessionView;
  /** origin=others 时快照所在分组的好友资料;mine 恒 null。 */
  friend: ConversationUserView | null;
}

/** 快照是否仍归属于当前选中身份;身份变化(换 Bot/归属/范围/会话/清空选中)
 *  即失效。是占位遮挡与主会话解析的唯一保留判据。 */
export function selectedSnapshotMatches(
  snapshot: SelectedSessionSnapshot | null,
  selection: {
    botId: string | null;
    section: ConversationBotSection | null;
    origin: ConversationOrigin;
    scope: ConversationSessionScope;
    friendUserId: string | null;
    sessionId: string | null;
  },
): snapshot is SelectedSessionSnapshot {
  if (!snapshot || !selection.botId || !selection.section || !selection.sessionId) return false;
  return (
    snapshot.section === selection.section &&
    snapshot.botId === selection.botId &&
    snapshot.origin === selection.origin &&
    snapshot.scope === selection.scope &&
    snapshot.friendUserId === selection.friendUserId &&
    snapshot.session.sessionId === selection.sessionId
  );
}

/** ConversationState 的纯数据字段(动作由 conversationStore.ts 实现)。 */
export type ConversationInitialData = Omit<
  ConversationState,
  | 'setExpandedBot'
  | 'setManagedBotOrigin'
  | 'setManagedBotScope'
  | 'setSessionKeyFilter'
  | 'setFriendBotScope'
  | 'setExpandedFriend'
  | 'setBotGroupCollapsed'
  | 'selectConversation'
  | 'setManagedBotCache'
  | 'setFriendBotSessions'
  | 'setSelectedSessionSnapshot'
  | 'reset'
>;

export const conversationInitialState: ConversationInitialData = {
  expandedBotIds: {},
  originByManagedBotId: {},
  scopeByManagedBotId: {},
  effectiveScopeByManagedBotId: {},
  expandedFriendUserIdsByBotId: {},
  collapsedBotGroups: {},
  sessionKeyByBotId: {},
  scopeByFriendBotId: {},
  sessionFilterRevisionByBotId: {},
  selectedBotId: null,
  selectedSection: null,
  selectedOrigin: 'mine',
  selectedScope: 'all',
  selectedFriendUserId: null,
  selectedSessionId: null,
  sessionsByBotId: {},
  friendBotSessionsByBotId: {},
  selectedSessionSnapshot: null,
};
