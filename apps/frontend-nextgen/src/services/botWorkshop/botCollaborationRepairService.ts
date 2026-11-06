import {
  repairBotCollaboration,
  type CollaborationRepairReport,
} from '@/services/backendApi/bots/botCollaborationRepairController';
import { isEnvelopeSuccess } from '@/services/backendApi/types';
import { parseCollaborationRepairReport } from './collaborationRepairReport';

export type { CollaborationRepairReport } from '@/services/backendApi/bots/botCollaborationRepairController';

/** 只保存在途 Promise：settle 即删除，不持久化修复状态、不重试、不轮询。 */
const pending = new Map<string, Promise<CollaborationRepairReport>>();
const key = (botId: string, userId: string) => JSON.stringify([userId, botId]);

export const botCollaborationRepairService = {
  pending(botId: string, userId: string) {
    return pending.get(key(botId, userId));
  },
  repair(botId: string, userId: string): Promise<CollaborationRepairReport> {
    const id = key(botId, userId);
    const active = pending.get(id);
    if (active) return active;
    const request = repairBotCollaboration(botId, userId)
      .then((response) => {
        if (!isEnvelopeSuccess(response)) throw new Error('Bot 信息修复失败');
        return parseCollaborationRepairReport(response.data);
      })
      .finally(() => {
        if (pending.get(id) === request) pending.delete(id);
      });
    pending.set(id, request);
    return request;
  },
};
