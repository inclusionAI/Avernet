// Conversation 领域类型(设计依据:docs/specs/2026-09-24-workspace-conversation-navigation-refactor.md §7)。
// 领域类型与纯 section 判定；依赖方向为 Service → 本模块,禁止反向运行时依赖。
// View 类型来自现有 Bot 单聊 Service 领域视图(仅 type 依赖,不引入运行时耦合)。
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import type { ChatMessage } from '@tc-chat/core';

// 会话 View 类型经本域收口再导出:Store 层禁止直连 Service(含 type import),
// 快照等 Store 侧类型统一从这里取(仅 type 依赖,不引入运行时耦合)。
export type { BotChatSessionView };

export type ConversationOrigin = 'mine' | 'others' | 'collab';
export type ConversationSessionScope = 'all' | 'favorite' | 'archived';
export type ConversationBotSection = 'managed' | 'team' | 'friend';

/** 团队空间 Bot 与管理 Bot 共用来源/范围及只读他人会话能力；friend 不参与。 */
export function isManagedConversationSection(
  section: ConversationBotSection | null | undefined,
): section is 'managed' | 'team' {
  return section === 'managed' || section === 'team';
}

export interface ConversationRouteState {
  /** 缺省表示尚未选择 Bot。入向兼容纯 bot_id，目录唯一匹配后规范为 botId；选中由 section + botId 区分。 */
  botId?: string;
  /** 缺省表示 section 尚未解析,由 Hook 的目录匹配推导;规范 URL 必须写出。 */
  section?: ConversationBotSection;
  /** 只对管理 / 团队空间 Bot 生效;缺省按 mine 处理;好友 Bot URL 不携带该字段。 */
  origin?: ConversationOrigin;
  /** 只对管理 / 团队空间 Bot 生效;缺省按 all 处理。非 Coding Bot 的 others 仍由 Store 强制 all。 */
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
  nextCursor?: string | null;
  scanLimited?: boolean;
}

/** 多实例服务 Bot 的会话请求范围:按实例(device UUID)与发布阶段取数;缺省不传 = 不限定(现状行为)。 */
export interface ConversationSessionRequestRange {
  /** 实例标识(containers 接口返回的实例 id,即 device UUID)。 */
  deviceUuid: string;
  /** 发布阶段;缺省由服务端默认。 */
  stage?: 'draft' | 'verify' | 'online';
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

export type CodingBotSessionArchiveSource = ConversationOrigin;
export type CodingBotSessionArchiveAvailability = 'unknown' | 'supported' | 'unsupported';
export type CodingBotSessionArchiveRestoreStatus = 'archived' | 'queued' | 'restoring' | 'succeeded' | 'failed';

export interface CodingBotSessionArchiveRestoreRequestSummary {
  sessionKey: string;
  archiveId: string;
  operationId: string;
  status: Extract<CodingBotSessionArchiveRestoreStatus, 'queued' | 'restoring' | 'succeeded' | 'failed'>;
  errorCode: string | null;
  updatedAt: string;
  sequence: number;
}

export interface CodingBotSessionArchiveItem {
  sessionKey: string;
  archiveId: string;
  title: string | null;
  sessionKind: 'user' | 'diagnosis' | null;
  createdAt: string | null;
  updatedAt: string | null;
  lastActiveAt: string | null;
  archivedAt: string | null;
  messageCount: number | null;
  restoredAt: string | null;
  restoreStatus: CodingBotSessionArchiveRestoreStatus;
  restore?: CodingBotSessionArchiveRestoreRequestSummary | null;
  /** 由 Coding Bot host 统一归类，主应用不得解析 sessionKey。 */
  sessionSource: CodingBotSessionArchiveSource;
}

export interface CodingBotSessionArchivePreviewMessage {
  role: 'user' | 'assistant';
  text: string;
  timestamp: string | null;
}

export interface CodingBotSessionArchivePreviewResultOk {
  ok: true;
  sessionKey: string;
  archiveId: string;
  available: boolean;
  incomplete: boolean;
  first: CodingBotSessionArchivePreviewMessage[];
  last: CodingBotSessionArchivePreviewMessage[];
}

export type CodingBotSessionArchivePreviewResult =
  | CodingBotSessionArchivePreviewResultOk
  | { ok: false; message: string };

export interface CodingBotRestoredSessionConversation {
  sessionId: string;
  title?: string;
  messageCount?: number;
  gmtModified?: string;
  gmtCreate?: string;
  model?: string;
}

export interface CodingBotRestoredSessionItem {
  sessionKey: string;
  archiveId: string;
  restoredAt: string;
  conversation?: CodingBotRestoredSessionConversation;
  sessionSource: CodingBotSessionArchiveSource;
}

export interface ConversationSessionArchiveListState {
  items: CodingBotSessionArchiveItem[];
  nextCursor: string | null;
  catalogRevision: number | null;
  loading: boolean;
  error: string | null;
  isLoadingMore: boolean;
  loadMoreError: string | null;
  availability: CodingBotSessionArchiveAvailability;
  restoreAvailability: CodingBotSessionArchiveAvailability;
  /** 正在轮询的恢复 operationId。 */
  operationIds: string[];
  /** 单次恢复提交是否在完成后自动进入会话；key 为 operationId。 */
  isSubmitting: boolean;
  restoreError: string | null;
  /** 可自动定位/高亮的最新恢复结果。 */
  restoredItems: CodingBotRestoredSessionItem[];
}

export interface ManagedBotConversationView {
  /** 保留 botId:ownerId 复合形态用于 UI key 与请求上下文;请求时拆 realBotId/ownerId。 */
  botId: string;
  origin: ConversationOrigin;
  scope: ConversationSessionScope;
  sessions: ConversationSessionListState;
  /** Coding Bot 专属：cursor 分页的归档列表；普通 Bot 不创建该状态。 */
  sessionArchives?: ConversationSessionArchiveListState;
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
