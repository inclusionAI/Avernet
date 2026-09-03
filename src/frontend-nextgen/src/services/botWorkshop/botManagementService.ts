import type { BotDomain } from '@/domain/botWorkshop';
import { createSpace, listSpaces } from '@/services/backendApi/admin/spaceController';
import { botCollaborationController } from '@/services/backendApi/bots/botCollaborationController';
import { changeBotSpace } from '@/services/backendApi/bots/botController';
import { botEditorController } from '@/services/backendApi/bots/botEditorController';

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

function mapCollaborator(item: { id: number; user_id: string; user_name?: string | null; role: 'admin' | 'member' }) {
  return { id: item.id, userId: item.user_id, name: item.user_name || item.user_id, role: item.role };
}

export const botManagementService = {
  stealEditLock: (bot: BotDomain) => botEditorController.stealEditLock(bot.id, bot.ownerId),
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
  async addCollaborator(botId: string, userId: string, name: string | undefined, role: BotCollaborator['role']) {
    const response = await botCollaborationController.add(botId, userId, name, role);
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
