// Conversation Store 的状态类型与初始状态(纯同步 setter 契约)。
// 设计依据:docs/specs/2026-09-24-workspace-conversation-navigation-refactor.md §7。
// Sidebar prop 的 ConversationState 语义以本文件导出的 ConversationState 为准。
import type {
  ConversationBotSection,
  ConversationOrigin,
  ConversationSessionListState,
  ConversationSessionScope,
  ManagedBotConversationView,
} from '@/domain/conversation';

// 缓存结构类型从 Domain 收口再导出,消费方(页面/组件/Hook)统一从 Store 取面向 UI 的类型。
export type {
  ConversationBotSection,
  ConversationOrigin,
  ConversationSessionListState,
  ConversationSessionScope,
  ManagedBotConversationView,
} from '@/domain/conversation';

export interface ConversationState {
  /** UI 同步状态:允许多个 Bot 同时展开。 */
  expandedBotIds: Record<string, true>;
  /** 按管理 Bot 记忆的发起归属;页面会话内记忆,不持久化。 */
  originByManagedBotId: Record<string, ConversationOrigin>;
  /** 按管理 Bot 记忆的 mine 会话范围;切到 others 不清除,切回 mine 恢复。 */
  scopeByManagedBotId: Record<string, ConversationSessionScope>;
  /** 当前生效的读取范围;origin=others 强制 all,其余等于记忆范围(缺省 all)。 */
  effectiveScopeByManagedBotId: Record<string, ConversationSessionScope>;
  /** 管理 Bot others 视角下,按 Bot 记忆的好友用户展开(key 为纯用户 ID,不带 human_)。 */
  expandedFriendUserIdsByBotId: Record<string, Record<string, true>>;

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

  setExpandedBot(botId: string, expanded: boolean): void;
  setManagedBotOrigin(botId: string, origin: ConversationOrigin): void;
  setManagedBotScope(botId: string, scope: ConversationSessionScope): void;
  setExpandedFriend(botId: string, friendUserId: string, expanded: boolean): void;
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
  reset(): void;
}

/** ConversationState 的纯数据字段(动作由 conversationStore.ts 实现)。 */
export type ConversationInitialData = Omit<
  ConversationState,
  | 'setExpandedBot'
  | 'setManagedBotOrigin'
  | 'setManagedBotScope'
  | 'setExpandedFriend'
  | 'selectConversation'
  | 'setManagedBotCache'
  | 'setFriendBotSessions'
  | 'reset'
>;

export const conversationInitialState: ConversationInitialData = {
  expandedBotIds: {},
  originByManagedBotId: {},
  scopeByManagedBotId: {},
  effectiveScopeByManagedBotId: {},
  expandedFriendUserIdsByBotId: {},
  selectedBotId: null,
  selectedSection: null,
  selectedOrigin: 'mine',
  selectedScope: 'all',
  selectedFriendUserId: null,
  selectedSessionId: null,
  sessionsByBotId: {},
  friendBotSessionsByBotId: {},
};
