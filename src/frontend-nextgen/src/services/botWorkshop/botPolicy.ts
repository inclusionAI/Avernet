import type { BotInventoryAction } from '@/domain/botWorkshop';
import type { BotAction, BotActionAvailability, BotDomain } from './types';

export interface BotPolicyContext {
  canEdit?: boolean;
  canView?: boolean;
  apiReady?: Partial<Record<BotAction, boolean>>;
}
const actions: BotAction[] = [
  'view',
  'edit',
  'chat',
  'publish',
  'offline',
  'restart',
  'logs',
  'instances',
  'evaluation',
  'activate',
  'claim-lock',
  'authorize',
];

export function getBotActionAvailability(bot: BotDomain, context: BotPolicyContext = {}): BotActionAvailability[] {
  return actions.map((action) => {
    let enabled = true;
    let disabledReason: string | undefined;
    let visible = true;
    if (action === 'logs') {
      if (bot.lifecycle === 'offline') {
        visible = false;
        enabled = false;
        disabledReason = 'Bot 已下线或回收';
      } else if (!bot.runtime.capabilityProfile.canViewLogs) {
        enabled = false;
        disabledReason = '当前 Bot 暂不支持日志查询';
      }
    }
    if (action === 'view' && context.canView === false) {
      enabled = false;
      disabledReason = '无查看权限';
    }
    if (action === 'edit' && context.canEdit === false) {
      enabled = false;
      disabledReason = '无编辑权限';
    }
    if (['edit', 'publish', 'offline', 'restart', 'activate', 'claim-lock', 'authorize'].includes(action)) {
      if (!bot.runtime.capabilityProfile.canEdit) {
        enabled = false;
        disabledReason = bot.runtime.engine === 'unknown' ? '引擎未识别' : '当前引擎不支持该操作';
      }
      if (bot.lifecycle === 'deploying') {
        enabled = false;
        disabledReason = 'Bot 部署中';
      }
    }
    if (bot.lock?.status === 'other' && ['edit', 'publish', 'offline', 'restart'].includes(action)) {
      enabled = false;
      disabledReason = '该 Bot 正被他人编辑，请先抢锁';
    }
    if (bot.lifecycle === 'offline' && !['view', 'activate'].includes(action)) {
      enabled = false;
      disabledReason = 'Bot 已下线或回收';
    }
    if (context.apiReady?.[action] === false) {
      enabled = false;
      disabledReason = '接口尚未接入，暂不能执行此操作';
    }
    return { action, visible, enabled, disabledReason, dangerous: ['offline', 'restart'].includes(action) };
  });
}

export function getInventoryActionAvailability(bot: BotDomain, action: BotInventoryAction): BotActionAvailability {
  const backendReason = bot.disabledActions[action];
  const declaredEnabled = bot.actions.includes(action);
  const visible = declaredEnabled || Boolean(backendReason);
  if (!visible) return { action: action as BotAction, visible: false, enabled: false };
  if (bot.lock?.status === 'other' && ['edit', 'restart'].includes(action)) {
    return {
      action: action as BotAction,
      visible: true,
      enabled: false,
      disabledReason: '该 Bot 正被他人编辑，请先抢锁',
    };
  }
  return {
    action: action as BotAction,
    visible: true,
    enabled: declaredEnabled,
    disabledReason: declaredEnabled ? undefined : backendReason,
  };
}

export function getBotCollaborationMode(bot: BotDomain, isOwner: boolean): 'authorize' | 'request' | undefined {
  if (bot.spaceKind !== 'team') return undefined;
  if (isOwner) return 'authorize';
  return bot.actions.includes('edit') ? undefined : 'request';
}
