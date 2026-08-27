import type {
  CreateSessionResult,
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
  listBots(query?: PublicBotSearchQuery, context?: HumanBotActionContext, signal?: AbortSignal): Promise<PublicBot[]>;
  discoverBots(
    query: PublicBotDiscoveryQuery,
    context?: HumanBotActionContext,
    signal?: AbortSignal,
  ): Promise<PublicBot[]>;
  getBotProfile(botId: string, signal?: AbortSignal): Promise<PublicBotProfile>;
  requestBotFriendship(botId: string, context: HumanBotActionContext): Promise<FriendRequestResult>;
  openBotConversation(botId: string, context: HumanBotActionContext): Promise<OpenBotConversationResult>;
  listGroups(query?: PublicGroupSearchQuery, signal?: AbortSignal): Promise<PublicGroup[]>;
  listGroupMembers(groupId: string, signal?: AbortSignal): Promise<PublicGroupMember[]>;
  createGroupSession(groupId: string): Promise<CreateSessionResult>;
}
