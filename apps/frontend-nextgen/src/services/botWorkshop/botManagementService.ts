import type { BotDomain } from '@/domain/botWorkshop';
import { createSpace, listSpaceMembers, listSpaces } from '@/services/backendApi/admin/spaceController';
import { botCollaborationController } from '@/services/backendApi/bots/botCollaborationController';
import { changeBotSpace } from '@/services/backendApi/bots/botController';
import { isEnvelopeFailure } from '@/services/backendApi/types';

export interface BotSpaceOption {
  id: number;
  name: string;
  type: string;
}
export interface BotCollaborator {
  id: number;
  userId: string;
  name: string;
  role: 'admin' | 'member';
}
export interface BotSpaceMember {
  userId: string;
  name: string;
}

const memberCache = new Map<string, { expires: number; members: BotSpaceMember[] }>();

function mapCollaborator(item: { id: number; user_id: string; user_name?: string | null; role: 'admin' | 'member' }) {
  return { id: item.id, userId: item.user_id, name: item.user_name || item.user_id, role: item.role };
}

export const botManagementService = {
  async listSpaces(userId: string): Promise<BotSpaceOption[]> {
    const response = await listSpaces({ user_id: userId, page_no: 1, page_size: 100, scope: 'accessible' });
    return (response.data?.items ?? []).flatMap((raw) => {
      const id = Number(raw.space_id ?? raw.id);
      if (!Number.isFinite(id)) return [];
      return [{ id, name: String(raw.space_name ?? raw.name ?? `空间 ${id}`), type: String(raw.space_type ?? '') }];
    });
  },
  async changeSpace(bot: BotDomain, spaceId: number, userId: string) {
    await changeBotSpace(bot.id, spaceId, userId);
  },
  async createTeamSpace(name: string, userId: string): Promise<BotSpaceOption> {
    const spaceName = name.trim();
    if (!spaceName) throw new Error('请输入新团队名称');
    const response = await createSpace({ space_name: spaceName }, { user_id: userId });
    const raw = response.data;
    const id = Number(raw?.space_id ?? raw?.id);
    if (!raw || !Number.isFinite(id)) throw new Error('创建团队接口未返回有效空间');
    return { id, name: String(raw.space_name ?? raw.name ?? spaceName), type: String(raw.space_type ?? 'TEAM') };
  },
  async listCollaborators(botId: string) {
    const response = await botCollaborationController.list(botId);
    return (response.data?.items ?? []).map(mapCollaborator);
  },
  async listSpaceMembers(spaceId: string, userId: string): Promise<BotSpaceMember[]> {
    const key = `${userId}:${spaceId}`;
    const cached = memberCache.get(key);
    if (cached && cached.expires > Date.now()) return cached.members;
    const members: BotSpaceMember[] = [];
    for (let page = 1; ; page += 1) {
      const response = await listSpaceMembers(spaceId, { user_id: userId, page_no: page, page_size: 100 });
      if (isEnvelopeFailure(response)) throw new Error(response.message || '加载空间成员失败');
      const items = response.data?.items ?? [];
      members.push(
        ...items.flatMap((raw) => {
          const id = String(raw.user_id ?? '').trim();
          if (!id) return [];
          return [{ userId: id, name: String(raw.display_name || raw.user_name || id) }];
        }),
      );
      if (items.length < 100 || members.length >= (response.data?.total ?? 0)) break;
    }
    memberCache.set(key, { expires: Date.now() + 60_000, members });
    return members;
  },
  async fillOwnerNames(items: BotDomain[], userId?: string): Promise<BotDomain[]> {
    if (!userId) return items;
    const spaceIds = [
      ...new Set(items.filter((item) => item.spaceKind === 'team' && item.spaceId).map((item) => item.spaceId!)),
    ];
    const groups = await Promise.all(
      spaceIds.map(async (id) => {
        try {
          return [id, await this.listSpaceMembers(id, userId)] as const;
        } catch {
          return [id, [] as BotSpaceMember[]] as const;
        }
      }),
    );
    const membersBySpace = new Map(groups);
    return items.map((item) => ({
      ...item,
      ownerName:
        item.ownerName ??
        membersBySpace.get(item.spaceId ?? '')?.find((member) => member.userId === item.ownerId)?.name,
    }));
  },
  async addCollaborator(botId: string, userId: string, name: string | undefined, role: BotCollaborator['role']) {
    const response = await botCollaborationController.add(botId, userId, name, role);
    if (isEnvelopeFailure(response)) throw new Error(response.message || '添加协作者失败');
    if (!response.data) throw new Error('添加成员接口未返回成员信息');
    return mapCollaborator(response.data);
  },
  async updateCollaborator(botId: string, id: number, role: BotCollaborator['role']) {
    const response = await botCollaborationController.update(botId, id, role);
    if (!response.data) throw new Error('角色更新接口未返回成员信息');
    return mapCollaborator(response.data);
  },
  removeCollaborator: (botId: string, id: number) => botCollaborationController.remove(botId, id),
  requestAccess: (bot: BotDomain, reason: string) => {
    if (!bot.ownerId) throw new Error('缺少 Bot Owner 信息，无法提交申请');
    return botCollaborationController.requestAccess(bot.id, bot.ownerId, reason);
  },
};
