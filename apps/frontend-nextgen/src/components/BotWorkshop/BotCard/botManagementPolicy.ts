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
    ? '该 Bot 发布失败，当前暂不支持删除，请先重试恢复。'
    : undefined;
}

export function botDeleteDisabledReason(bot: BotDomain, lockedByOther: boolean) {
  if (lockedByOther) return '该 Bot 正被他人编辑，暂时无法删除。';
  if (bot.actions.includes('delete')) return undefined;
  if (bot.lifecycle === 'deploying') return 'Bot 正在部署，暂时无法删除，请等待部署结束。';
  const reason = bot.disabledActions.delete;
  if (reason) return reason;
  return failedServiceDeleteReason(bot) ?? '当前状态暂不支持删除该 Bot。';
}
