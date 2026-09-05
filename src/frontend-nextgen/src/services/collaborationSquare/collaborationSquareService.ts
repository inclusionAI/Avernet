import type {
  BotCatalogViewer,
  FriendRequestActor,
  HumanBotActionContext,
  PublicBotDiscoveryQuery,
  PublicBotSearchQuery,
  PublicGroupSearchQuery,
  PublicTaskSearchQuery,
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
  async resolveSharedBot(
    targetId: string,
    searchHint: string,
    context?: HumanBotActionContext,
    viewer?: BotCatalogViewer,
    signal?: AbortSignal,
  ) {
    const id = targetId.trim();
    const search = searchHint.trim();
    if (!id || !search) return null;

    const pageSize = 100;
    let page = 1;
    while (!signal?.aborted) {
      const result = await this.gateway.listBotPage(
        {
          search,
          page,
          pageSize,
          ...(viewer
            ? { viewerActorType: viewer.viewerActorType, viewerActorId: viewer.viewerActorId }
            : {}),
        },
        context,
        signal,
      );
      const matched = result.items.find((bot) => bot.id === id);
      if (matched) return matched;
      if (page * pageSize >= result.total || result.items.length === 0) return null;
      page += 1;
    }
    return null;
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
  listPublicTasks(query?: PublicTaskSearchQuery, signal?: AbortSignal) {
    return this.gateway.listPublicTasks(query, signal);
  }
  getPublicTask(taskId: string, signal?: AbortSignal) {
    return this.gateway.getPublicTask(taskId, signal);
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
// 任务广场：接入真实 BBS 任务列表端点 GET /api/v1/collaboration/tasks/bbs/list，与 bot/group 一致走 ApiAdapter。
// Mock 的 task 方法/fixture 保留（不再被 wired service 使用，留作 dev/测试兜底，不删）。
export const collaborationSquareTaskService = new CollaborationSquareService(new CollaborationSquareApiAdapter());
