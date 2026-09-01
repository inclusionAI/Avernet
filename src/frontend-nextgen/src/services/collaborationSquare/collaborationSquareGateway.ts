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
}
