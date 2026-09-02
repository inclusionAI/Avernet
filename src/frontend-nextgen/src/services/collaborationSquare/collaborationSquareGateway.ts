import type {
  CollaborationSquarePage,
  CreateSessionResult,
  FriendRequestActor,
  FriendRequestResult,
  HumanBotActionContext,
  OpenBotConversationResult,
  PublicBot,
  PublicBotDiscoveryQuery,
  PublicBotProfile,
  PublicBotSearchQuery,
  PublicGroup,
  PublicGroupMember,
  PublicGroupSearchQuery,
  PublicTask,
  PublicTaskPage,
  PublicTaskSearchQuery,
} from '@/domain/collaborationSquare/types';

export interface CollaborationSquareGateway {
  listBotPage(
    query?: PublicBotSearchQuery,
    context?: HumanBotActionContext,
    signal?: AbortSignal,
  ): Promise<CollaborationSquarePage<PublicBot>>;
  listBots(query?: PublicBotSearchQuery, context?: HumanBotActionContext, signal?: AbortSignal): Promise<PublicBot[]>;
  discoverBots(
    query: PublicBotDiscoveryQuery,
    context?: HumanBotActionContext,
    signal?: AbortSignal,
  ): Promise<PublicBot[]>;
  getBotProfile(botId: string, signal?: AbortSignal): Promise<PublicBotProfile>;
  requestBotFriendship(
    botId: string,
    context: HumanBotActionContext,
    friendRequestBotId?: string,
    fromActor?: FriendRequestActor,
  ): Promise<FriendRequestResult>;
  openBotConversation(botId: string, context: HumanBotActionContext): Promise<OpenBotConversationResult>;
  listGroupPage(query?: PublicGroupSearchQuery, signal?: AbortSignal): Promise<CollaborationSquarePage<PublicGroup>>;
  listGroups(query?: PublicGroupSearchQuery, signal?: AbortSignal): Promise<PublicGroup[]>;
  listGroupMembers(groupId: string, signal?: AbortSignal): Promise<PublicGroupMember[]>;
  createGroupSession(
    groupId: string,
    context?: HumanBotActionContext,
    options?: { title?: string; query?: string },
  ): Promise<CreateSessionResult>;
  /** 浏览/搜索公开任务广场（跨用户公开，本期仅 Mock/Unsupported，真实端点待后端建设）。 */
  listPublicTasks(query?: PublicTaskSearchQuery, signal?: AbortSignal): Promise<PublicTaskPage>;
  /** 任务只读详情；目标不存在或状态未知时抛 `target_invalid`。 */
  getPublicTask(taskId: string, signal?: AbortSignal): Promise<PublicTask>;
}
