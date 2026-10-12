import type { BotManagementVerb } from '@/domain/botWorkshop';
import { botWorkshopService } from './botWorkshopService';
import { localBotService } from './localBotService';
import type { BotDomain } from './types';

const runners: Record<BotManagementVerb, (bot: BotDomain) => Promise<void>> = {
  open_folder: async (bot) => {
    await localBotService.openFolder(bot.id);
  },
  delete: (bot) => botWorkshopService.remove(bot),
  restart: (bot) => botWorkshopService.restart(bot),
  engine_restart: (bot) =>
    bot.deployment === 'local' ? botWorkshopService.restart(bot) : botWorkshopService.restartEngine(bot.id),
  upgrade: (bot) => botWorkshopService.enableService(bot.id),
  restart_publish: (bot) => botWorkshopService.restartPublish(bot),
};

const successMessages: Record<BotManagementVerb, string> = {
  open_folder: '打开目录请求已提交',
  delete: 'Bot 已删除',
  restart: '重启请求已提交',
  engine_restart: '重启请求已提交',
  upgrade: '已开启服务化',
  restart_publish: '重启发布已提交',
};

export const botManagementActionService = {
  run: (action: BotManagementVerb, bot: BotDomain) => runners[action](bot),
  successMessage: (action: BotManagementVerb) => successMessages[action],
};
