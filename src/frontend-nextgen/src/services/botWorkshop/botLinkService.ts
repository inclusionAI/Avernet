import type { BotLinkInput } from '@/domain/botLinks';
import { botLinkController } from '@/services/backendApi/bots/botLinkController';
export const botLinkService = {
  async list(botId: string) {
    const response = await botLinkController.list(botId);
    if (!response.data) throw new Error('关联链接未返回数据');
    return response.data;
  },
  add: (botId: string, items: BotLinkInput[]) => botLinkController.create(botId, items),
  update: botLinkController.update,
  remove: botLinkController.remove,
};
