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
import {
  mapPublicTaskDto,
  sortPublicTasksByPublishedDesc,
  type PublicTaskTransport,
} from '@/domain/collaborationSquare/taskMapper';
import type {
  BotRelationshipStatus,
  CreateSessionResult,
  HumanBotActionContext,
  PublicBot,
  PublicBotDiscoveryQuery,
  PublicBotSearchQuery,
  PublicGroup,
  PublicGroupSearchQuery,
  PublicTask,
  PublicTaskPage,
  PublicTaskSearchQuery,
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

  // 任务广场：本期仅 Mock（后端公开跨用户 BBS 任务广场端点 Out of Scope 未建设），跨用户公开，不做 owner 过滤。
  async listPublicTasks(query: PublicTaskSearchQuery = {}, signal?: AbortSignal): Promise<PublicTaskPage> {
    // 可控失败触发（仅浏览器，对齐既有 mock 的 simulate 范式）；node 测试无 window 不触发。
    if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('simulate') === 'task-fail') {
      throw new CollaborationSquareError('network', '任务广场数据加载失败');
    }
    const dtos = await readJson<PublicTaskTransport[]>('/api/mock/collaboration-square/tasks', signal);
    const all = dtos.map(mapPublicTaskDto).filter((task): task is PublicTask => task !== null);
    const keyword = (query.search ?? '').trim().toLocaleLowerCase();
    const matched = keyword
      ? all.filter(
          (task) => task.name.toLocaleLowerCase().includes(keyword) || task.goal.toLocaleLowerCase().includes(keyword),
        )
      : all;
    const filtered =
      query.status && query.status !== 'all' ? matched.filter((task) => task.status === query.status) : matched;
    // 与真实 adapter 一致：客户端过滤后按 publishedAt 倒序（最新发布在前），再分页。
    const sorted = sortPublicTasksByPublishedDesc(filtered);
    const offset = Math.max(0, query.offset ?? 0);
    const limit = query.limit ?? sorted.length;
    return { items: structuredClone(sorted.slice(offset, offset + limit)), total: sorted.length };
  }

  async getPublicTask(taskId: string, signal?: AbortSignal): Promise<PublicTask> {
    const dto = await readJson<PublicTaskTransport>(
      `/api/mock/collaboration-square/tasks/${encodeURIComponent(taskId)}`,
      signal,
    );
    const task = mapPublicTaskDto(dto);
    if (!task) throw new CollaborationSquareError('target_invalid', '内容已取消公开或不可访问');
    return task;
  }
}
