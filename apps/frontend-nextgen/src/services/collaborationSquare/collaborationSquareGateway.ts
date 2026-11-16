import type { MessageViewScope } from '@/domain/collaboration/types';
import type {
  BotCandidatePage,
  CollaborationSquarePage,
  CreateSessionResult,
  FriendRequestActor,
  FriendRequestResult,
  HumanBotActionContext,
  OpenBotConversationOptions,
  OpenBotConversationResult,
  PublicBot,
  PublicBotDiscoveryQuery,
  PublicBotDiscoveryResult,
  PublicBotProfile,
  PublicBotSearchQuery,
  PublicGroup,
  PublicGroupMembersDetail,
  PublicGroupSearchQuery,
  PublicTask,
  PublicTaskPage,
  PublicTaskSearchQuery,
} from '@/domain/collaborationSquare/types';

export interface CollaborationSquareGateway {
  /** 公开 Bot 普通列表：BCS 原始 offset 游标页（经可选 Backend metadata 过滤，不返回 total）。 */
  listBotPage(query?: PublicBotSearchQuery, context?: HumanBotActionContext, signal?: AbortSignal): Promise<BotCandidatePage>;
  listBots(query?: PublicBotSearchQuery, context?: HumanBotActionContext, signal?: AbortSignal): Promise<PublicBot[]>;
  /** 智能搜索：BCS 内部 candidates/search 的一次性批次；items 之外独立携带 wire 的 search_mode。 */
  discoverBots(
    query: PublicBotDiscoveryQuery,
    context?: HumanBotActionContext,
    signal?: AbortSignal,
  ): Promise<PublicBotDiscoveryResult>;
  /** 申请弹窗画像：从已点击的 `PublicBot` 摘要出发，Backend 增强存在才读旧 profile 端点。 */
  getBotProfile(bot: PublicBot, signal?: AbortSignal): Promise<PublicBotProfile>;
  requestBotFriendship(
    botId: string,
    context: HumanBotActionContext,
    friendRequestBotId?: string,
    fromActor?: FriendRequestActor,
  ): Promise<FriendRequestResult>;
  openBotConversation(
    botId: string,
    context: HumanBotActionContext,
    options?: OpenBotConversationOptions,
  ): Promise<OpenBotConversationResult>;
  listGroupPage(query?: PublicGroupSearchQuery, signal?: AbortSignal): Promise<CollaborationSquarePage<PublicGroup>>;
  listGroups(query?: PublicGroupSearchQuery, signal?: AbortSignal): Promise<PublicGroup[]>;
  listGroupMembers(groupId: string, signal?: AbortSignal): Promise<PublicGroupMembersDetail>;
  createGroupSession(
    groupId: string,
    context?: HumanBotActionContext,
    options?: { title?: string; query?: string; messageViewScope?: MessageViewScope },
  ): Promise<CreateSessionResult>;
  /** 浏览/搜索公开任务广场（跨用户公开，本期仅 Mock/Unsupported，真实端点待后端建设）。 */
  listPublicTasks(query?: PublicTaskSearchQuery, signal?: AbortSignal): Promise<PublicTaskPage>;
  /** 任务只读详情；目标不存在或状态未知时抛 `target_invalid`。 */
  getPublicTask(taskId: string, signal?: AbortSignal): Promise<PublicTask>;
}
