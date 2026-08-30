import type { BotDomain } from '@/domain/botWorkshop';
import { listSpaces } from '@/services/backendApi/admin/spaceController';
import { botCollaborationController } from '@/services/backendApi/bots/botCollaborationController';
import { changeBotSpace } from '@/services/backendApi/bots/botController';
import { botEditorController, type EditLockDto } from '@/services/backendApi/bots/botEditorController';

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

function lockTime(lock: EditLockDto) {
  return lock.locked_at ?? lock.acquired_at ?? lock.created_at ?? undefined;
}

export const botManagementService = {
  async loadServiceLocks(items: BotDomain[], currentUserId?: string): Promise<BotDomain[]> {
    const targets = [
      ...new Map(
        items.filter((bot) => bot.serviceMode === 'service' && bot.lifecycle === 'draft').map((bot) => [bot.id, bot]),
      ).values(),
    ];
    const locks = new Map<string, EditLockDto>();
    await Promise.all(
      targets.map(async (bot) => {
        try {
          const response = await botEditorController.getEditLock(bot.id, bot.ownerId);
          if (response.data?.locked) locks.set(bot.id, response.data);
        } catch {
          // 锁状态不影响 Bot 主列表可用性；接口失败时按无锁摘要降级展示。
        }
      }),
    );
    return items.map((bot) => {
      const lock = locks.get(bot.id);
      if (!lock) return bot;
      const holderUserId = lock.holder_user_id ?? undefined;
      return {
        ...bot,
        lock: {
          status: holderUserId && currentUserId && holderUserId === currentUserId ? 'mine' : 'other',
          holderUserId,
          holderName: lock.holder_name ?? undefined,
          lockedAt: lockTime(lock),
        },
      };
    });
  },
  stealEditLock: (bot: BotDomain) => botEditorController.stealEditLock(bot.id, bot.ownerId),
  async listSpaces(userId: string): Promise<BotSpaceOption[]> {
    const response = await listSpaces({ user_id: userId, page_no: 1, page_size: 100 });
    return (response.data?.items ?? []).flatMap((raw) => {
      const id = Number(raw.space_id ?? raw.id);
      if (!Number.isFinite(id)) return [];
      return [{ id, name: String(raw.space_name ?? raw.name ?? `空间 ${id}`), type: String(raw.space_type ?? '') }];
    });
  },
  async changeSpace(bot: BotDomain, spaceId: number, userId: string) {
    await changeBotSpace(bot.id, spaceId, userId);
  },
  async listCollaborators(botId: string) {
    const response = await botCollaborationController.list(botId);
    return (response.data?.items ?? []).map((item) => ({
      id: item.id,
      userId: item.user_id,
      name: item.user_name || item.user_id,
      role: item.role,
    }));
  },
  addCollaborator: (botId: string, userId: string, role: BotCollaborator['role']) =>
    botCollaborationController.add(botId, userId, role),
  updateCollaborator: (botId: string, id: number, role: BotCollaborator['role']) =>
    botCollaborationController.update(botId, id, role),
  removeCollaborator: (botId: string, id: number) => botCollaborationController.remove(botId, id),
  requestAccess: (bot: BotDomain, reason: string) => {
    if (!bot.ownerId) throw new Error('缺少 Bot Owner 信息，无法提交申请');
    return botCollaborationController.requestAccess(bot.id, bot.ownerId, reason);
  },
};
