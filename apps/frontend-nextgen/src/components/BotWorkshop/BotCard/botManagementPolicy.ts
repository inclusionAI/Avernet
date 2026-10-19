import type { BotDomain } from '@/services/botWorkshop';

const publicationActions = ['publish_staging', 'publish_online', 'cancel_staging', 'offline', 'retry', 'upgrade'];

export function publicationManagementAvailability(bot: BotDomain) {
  const declared = publicationActions.some((action) => bot.actions.includes(action) || Boolean(bot.disabledActions[action]));
  if (bot.lifecycle === 'deploying') {
    return { visible: true, enabled: false, disabledReason: 'Bot 部署中，暂不能推进发布阶段' };
  }
  if (bot.lifecycle === 'failed' && !declared) return { visible: false, enabled: false };
  return { visible: true, enabled: true };
}

/** 创建失败尚未进入发布生命周期时，可由通用容器重启接口重新拉起。 */
export function canRestartFailedServiceBot(bot: BotDomain) {
  return bot.serviceMode === 'service' && bot.rawStatus?.toUpperCase() === 'FAILED';
}

export function failedServiceDeleteReason(bot: BotDomain) {
  return bot.serviceMode === 'service' && bot.lifecycle === 'failed' && !bot.actions.includes('delete')
    ? '发布失败的服务 Bot 需先重试恢复；当前后端不支持直接删除失败发布记录'
    : undefined;
}
