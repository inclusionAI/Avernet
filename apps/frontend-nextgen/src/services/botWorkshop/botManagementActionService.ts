import type { BotManagementVerb } from '@/domain/botWorkshop';
import { botWorkshopService } from './botWorkshopService';
import type { BotRestartResponse } from './botRestartTracking';
import { localBotService } from './localBotService';
import type { BotDomain } from './types';
import { botDormantService } from './botDormantService';
import type { BotActivationResult } from '@/services/backendApi/bots/botDormantController';

const runners: Record<
  BotManagementVerb,
  (bot: BotDomain, userId?: string) => Promise<void | BotActivationResult | BotRestartResponse>
> = {
  activate: (bot, userId) => botDormantService.activate(bot, userId ?? ''),
  open_folder: async (bot) => {
    await localBotService.openFolder(bot.id);
  },
  delete: (bot) => botWorkshopService.remove(bot),
  restart: (bot, userId) => botWorkshopService.restart(bot, userId),
  // Coding Bot暂时以 engine_restart 作为入口，但实际预设转发 Relay refetch 语义。
  engine_restart: (bot) =>
    bot.runtime.isAgentCodingBot
      ? botWorkshopService.refetchEngine(bot.id)
      : bot.deployment === 'local'
      ? botWorkshopService.restart(bot)
      : botWorkshopService.restartEngine(bot.id),
  upgrade: (bot) => botWorkshopService.enableService(bot.id),
  restart_publish: (bot) => botWorkshopService.restartPublish(bot),
};

const successMessages: Record<BotManagementVerb, string> = {
  activate: '激活请求已提交，请等待 Bot 恢复运行',
  open_folder: '打开目录请求已提交',
  delete: 'Bot 已删除',
  restart: '重启请求已提交',
  engine_restart: '重启请求已提交',
  upgrade: '已开启服务化',
  restart_publish: '重启发布已提交',
};

export const botManagementActionService = {
  run: (action: BotManagementVerb, bot: BotDomain, userId?: string) => runners[action](bot, userId),
  successMessage: (action: BotManagementVerb) => successMessages[action],
};
