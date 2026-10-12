import type { BotInventoryAction } from '@/domain/botWorkshop';
import type { BotAction, BotActionAvailability, BotDomain } from './types';

export interface BotPolicyContext {
  canEdit?: boolean;
  canView?: boolean;
  apiReady?: Partial<Record<BotAction, boolean>>;
}

/**
 * 进入编辑/查询详情页必须是 Owner 或后端明确授予 edit 的协作者。
 * 单独的 view action 只允许查看列表基础信息，不能触发详情页的子资源请求。
 */
export function canEnterBotDetail(bot: BotDomain, currentUserId?: string): boolean {
  if (!currentUserId) return false;
  return bot.ownerId === currentUserId || bot.actions.includes('edit');
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

const blockedEntryReasons: Partial<Record<BotDomain['lifecycle'], string>> = {
  deploying: 'Bot 部署中，暂不能进入',
  prestable: 'Bot 预发布中，暂不能进入',
  offline: 'Bot 已离线，请恢复运行后再进入',
  failed: 'Bot 状态异常，请修复后再进入',
  unknown: 'Bot 状态未知，请刷新后重试',
};

export function getBotEntryAvailability(bot: BotDomain): BotActionAvailability {
  const disabledReason = blockedEntryReasons[bot.lifecycle];
  return {
    action: 'view',
    visible: true,
    enabled: !disabledReason,
    disabledReason,
  };
}

export function getBotActionAvailability(bot: BotDomain, context: BotPolicyContext = {}): BotActionAvailability[] {
  return actions.map((action) => {
    let enabled = true;
    let disabledReason: string | undefined;
    let visible = true;
    if (['view', 'edit', 'chat'].includes(action)) {
      const entry = getBotEntryAvailability(bot);
      if (!entry.enabled) {
        enabled = false;
        disabledReason = entry.disabledReason;
      }
    }
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
  if (['view', 'edit', 'chat'].includes(action)) {
    const entry = getBotEntryAvailability(bot);
    if (!entry.enabled) {
      return {
        action: action as BotAction,
        visible: true,
        enabled: false,
        disabledReason: entry.disabledReason,
      };
    }
  }
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

/**
 * 通用配置菜单项可用性（collab-permission-entry-migration AC-02/AC-03）：
 * 仅 Bot 管理员（Owner）可变更通用配置；他人持编辑锁时同样禁用。
 * TODO：团队空间下「Owner 非当前用户但已获操作权限」的 Bot 是否放开，待对应模块决策后修订（Spec 2026-09-24 AC-02 登记）。
 */
export function getGeneralConfigAvailability(
  isOwner: boolean,
  lockedByOther: boolean,
): { enabled: boolean; disabledReason?: string } {
  if (!isOwner) return { enabled: false, disabledReason: '仅 Bot 管理员可变更通用配置' };
  if (lockedByOther) return { enabled: false, disabledReason: '该 Bot 正被他人编辑，请先抢锁' };
  return { enabled: true };
}
