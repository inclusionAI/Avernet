import {
  filterPublicBots,
  filterPublicGroups,
  mapBotProfileTransport,
  mapBotTransport,
  mapGroupMembersTransport,
  mapGroupTransport,
  type BotProfileTransport,
  type PublicBotTransport,
  type PublicGroupMemberTransport,
  type PublicGroupTransport,
} from '@/domain/collaborationSquare/mapper';
import type {
  BotRelationshipStatus,
  CreateSessionResult,
  HumanBotActionContext,
  PublicBot,
  PublicBotDiscoveryQuery,
  PublicBotSearchQuery,
  PublicGroup,
  PublicGroupSearchQuery,
} from '@/domain/collaborationSquare/types';
import { CollaborationSquareError } from './collaborationSquareError';
import type { CollaborationSquareGateway } from './collaborationSquareGateway';

const delay = (duration: number) =>
  new Promise((resolve) => {
    setTimeout(resolve, duration);
  });

async function readJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (response.status === 404) throw new CollaborationSquareError('target_invalid', '内容已取消公开或不可访问');
  if (!response.ok) throw new CollaborationSquareError('network', '协作广场数据加载失败');
  return response.json() as Promise<T>;
}

export class MockCollaborationSquareAdapter implements CollaborationSquareGateway {
  private bots: PublicBot[] = [];
  private groups: PublicGroup[] = [];

  async listBots(_query?: PublicBotSearchQuery, _context?: HumanBotActionContext, signal?: AbortSignal) {
    return (await this.listBotPage(_query, _context, signal)).items;
  }

  async listBotPage(_query?: PublicBotSearchQuery, _context?: HumanBotActionContext, signal?: AbortSignal) {
    this.bots = (await readJson<PublicBotTransport[]>('/api/mock/collaboration-square/bots', signal)).map(
      mapBotTransport,
    );
    return { items: structuredClone(this.bots), total: this.bots.length };
  }

  async discoverBots(query: PublicBotDiscoveryQuery, context?: HumanBotActionContext, signal?: AbortSignal) {
    return filterPublicBots(await this.listBots(undefined, context, signal), query.keyword, 'smart');
  }

  async getBotProfile(botId: string, signal?: AbortSignal) {
    return mapBotProfileTransport(
      await readJson<BotProfileTransport>(`/api/mock/collaboration-square/bots/${encodeURIComponent(botId)}`, signal),
    );
  }

  async requestBotFriendship(botId: string, _context: HumanBotActionContext, _friendRequestBotId?: string) {
    void _context;
    void _friendRequestBotId;
    await delay(420);
    const bot = this.bots.find((item) => item.id === botId);
    if (!bot) throw new CollaborationSquareError('target_invalid', '内容已取消公开或不可访问');
    if (
      botId === 'sample-product-bot' &&
      typeof window !== 'undefined' &&
      new URLSearchParams(window.location.search).get('simulate') === 'scope-denied'
    ) {
      throw new CollaborationSquareError('scope_not_matched', '当前组织不在该 Bot 的好友申请范围内');
    }
    const next: BotRelationshipStatus = botId === 'sample-product-bot' ? 'applying' : 'friend';
    bot.relationshipStatus = next;
    return { status: next };
  }

  async openBotConversation(botId: string, _context: HumanBotActionContext) {
    void _context;
    await delay(320);
    const bot = this.bots.find((item) => item.id === botId);
    if (!bot) throw new CollaborationSquareError('target_invalid', '内容已取消公开或不可访问');
    if (bot.relationshipStatus !== 'friend') throw new CollaborationSquareError('unknown', '好友关系尚未建立');
    return { sessionId: `bot-${botId}` };
  }

  async listGroups(query: PublicGroupSearchQuery = {}, signal?: AbortSignal) {
    return (await this.listGroupPage(query, signal)).items;
  }

  async listGroupPage(query: PublicGroupSearchQuery = {}, signal?: AbortSignal) {
    this.groups = (await readJson<PublicGroupTransport[]>('/api/mock/collaboration-square/groups', signal)).map(
      mapGroupTransport,
    );
    const groups = filterPublicGroups(this.groups, query.search ?? '');
    return { items: structuredClone(groups), total: groups.length };
  }

  async listGroupMembers(groupId: string, signal?: AbortSignal) {
    if (!this.groups.some((item) => item.id === groupId))
      throw new CollaborationSquareError('target_invalid', '内容已取消公开或不可访问');
    return mapGroupMembersTransport(
      await readJson<PublicGroupMemberTransport[]>(
        `/api/mock/collaboration-square/groups/${encodeURIComponent(groupId)}/members`,
        signal,
      ),
    );
  }

  async createGroupSession(groupId: string): Promise<CreateSessionResult> {
    await delay(450);
    if (!this.groups.some((item) => item.id === groupId))
      throw new CollaborationSquareError('target_invalid', '内容已取消公开或不可访问');
    return {
      sessionId: `square-${groupId}-${Date.now()}`,
    };
  }
}
