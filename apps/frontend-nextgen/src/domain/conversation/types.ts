// Conversation 领域类型(设计依据:docs/specs/2026-09-24-workspace-conversation-navigation-refactor.md §7)。
// 仅为纯类型模块:不包含运行时逻辑,依赖方向为 Service → 本模块,禁止反向。
// View 类型来自现有 Bot 单聊 Service 领域视图(仅 type 依赖,不引入运行时耦合)。
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import type { ChatMessage } from '@tc-chat/core';

export type ConversationOrigin = 'mine' | 'others';
export type ConversationSessionScope = 'all' | 'favorite';
export type ConversationBotSection = 'managed' | 'friend';

export interface ConversationRouteState {
  /** 缺省表示尚未选择 Bot;选中后由 section + botId 区分管理 Bot 和好友 Bot。 */
  botId?: string;
  /** 缺省表示 section 尚未解析,由 Hook 的目录匹配推导;规范 URL 必须写出。 */
  section?: ConversationBotSection;
  /** 只对管理 Bot 生效;缺省按 mine 处理;好友 Bot URL 不携带该字段。 */
  origin?: ConversationOrigin;
  /** 只对管理 Bot 的 origin=mine 生效;缺省按 all 处理;others 强制按 all 处理。 */
  scope?: ConversationSessionScope;
  /** 纯用户 ID(归一化移除 human_ 前缀);仅 origin=others 使用。 */
  friendUserId?: string;
  sessionId?: string;
}

export interface ConversationBotView {
  bot: ChatBotView;
  section: ConversationBotSection;
}

export interface ConversationUserView {
  userId: string;
  /** 详情缺失时由 Service 回退 userId。 */
  displayName: string;
}

export interface ConversationSessionListState {
  items: BotChatSessionView[];
  page: number;
  total: number;
  hasMore: boolean;
  loading: boolean;
  error: string | null;
  isLoadingMore: boolean;
  loadMoreError: string | null;
}

export interface ConversationFriendGroupView {
  friend: ConversationUserView;
  state: 'unloaded' | 'loading' | 'loaded' | 'error';
  sessions: ConversationSessionListState;
  expanded: boolean;
  /** state=error 时的用户可读错误信息。 */
  error?: string;
}

export interface ConversationFriendDirectoryState {
  items: ConversationUserView[];
  loading: boolean;
  error: string | null;
}

export interface ManagedBotConversationView {
  /** 保留 botId:ownerId 复合形态用于 UI key 与请求上下文;请求时拆 realBotId/ownerId。 */
  botId: string;
  origin: ConversationOrigin;
  scope: ConversationSessionScope;
  sessions: ConversationSessionListState;
  friendDirectory: ConversationFriendDirectoryState;
  /** key 为纯用户 ID(不带 human_)。 */
  friendGroups: Record<string, ConversationFriendGroupView>;
}

/** 只读消息分页视图,由 managedBotConversationService.listOtherMessages 产出(仅读,不创建)。 */
export interface ConversationMessagePageView {
  /** 与 mapBotSessionMessages 产出的消息视图同一形态(旧→新升序)。 */
  messages: ChatMessage[];
  page: number;
  /** 后端会话消息总数。 */
  total: number;
  /** 本页原始 DTO 条数(未受消息映射影响)。 */
  rawCount: number;
  /** 由原始 count/total 计算:page * pageSize < total。 */
  hasMore: boolean;
}
