import type {
  FriendRequestActor,
  HumanBotActionContext,
  PublicBotDiscoveryQuery,
  PublicBotSearchQuery,
  PublicGroupSearchQuery,
} from '@/domain/collaborationSquare/types';
import { listGroups as listBackendGroups, queryCollaborationBots } from '@/services/backendApi';
import { CollaborationSquareApiAdapter } from './collaborationSquareApiAdapter';
import { CollaborationSquareError } from './collaborationSquareError';
import type { CollaborationSquareGateway } from './collaborationSquareGateway';
import { MockCollaborationSquareAdapter } from './mockCollaborationSquareAdapter';

export { CollaborationSquareError } from './collaborationSquareError';

export interface CollaborationSquareServiceOverview {
  module: string;
  description: string;
}

export class CollaborationSquareService {
  private readonly inFlight = new Set<string>();
  constructor(private readonly gateway: CollaborationSquareGateway) {}

  getOverview(): CollaborationSquareServiceOverview {
    void listBackendGroups;
    void queryCollaborationBots;
    return {
      module: 'collaborationSquare',
      description: '协作广场 Service 已保留 collaboration Controller 接缝，当前功能通过可替换 Adapter 提供数据。',
    };
  }

  listBots(query?: PublicBotSearchQuery, context?: HumanBotActionContext, signal?: AbortSignal) {
    return this.gateway.listBots(query, context, signal);
  }
  listBotPage(query?: PublicBotSearchQuery, context?: HumanBotActionContext, signal?: AbortSignal) {
    return this.gateway.listBotPage(query, context, signal);
  }
  discoverBots(query: PublicBotDiscoveryQuery, context?: HumanBotActionContext, signal?: AbortSignal) {
    return this.gateway.discoverBots(query, context, signal);
  }
  getBotProfile(botId: string, signal?: AbortSignal) {
    return this.gateway.getBotProfile(botId, signal);
  }
  listGroups(query?: PublicGroupSearchQuery, signal?: AbortSignal) {
    return this.gateway.listGroups(query, signal);
  }
  listGroupPage(query?: PublicGroupSearchQuery, signal?: AbortSignal) {
    return this.gateway.listGroupPage(query, signal);
  }
  listGroupMembers(groupId: string, signal?: AbortSignal) {
    return this.gateway.listGroupMembers(groupId, signal);
  }

  private async runTargetAction<T>(key: string, task: () => Promise<T>) {
    if (this.inFlight.has(key)) throw new CollaborationSquareError('duplicate_action', '该操作正在提交，请勿重复操作');
    this.inFlight.add(key);
    try {
      return await task();
    } finally {
      this.inFlight.delete(key);
    }
  }

  requestBotFriendship(
    botId: string,
    context: HumanBotActionContext,
    friendRequestBotId?: string,
    fromActor?: FriendRequestActor,
  ) {
    const targetId = friendRequestBotId?.trim() || botId;
    return this.runTargetAction(`friend:${targetId}`, () =>
      this.gateway.requestBotFriendship(botId, context, friendRequestBotId, fromActor),
    );
  }

  openBotConversation(botId: string, context: HumanBotActionContext) {
    return this.runTargetAction(`conversation:${botId}`, () => this.gateway.openBotConversation(botId, context));
  }

  createGroupSession(groupId: string, context?: HumanBotActionContext, options?: { title?: string; query?: string }) {
    return this.runTargetAction(`session:${groupId}`, () => this.gateway.createGroupSession(groupId, context, options));
  }
}

export const collaborationSquareService = new CollaborationSquareService(new MockCollaborationSquareAdapter());
export const collaborationSquareBotService = new CollaborationSquareService(new CollaborationSquareApiAdapter());
export const collaborationSquareGroupService = new CollaborationSquareService(new CollaborationSquareApiAdapter());
