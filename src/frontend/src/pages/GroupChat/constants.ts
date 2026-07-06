import type { GroupStrategy } from './types';

export const BCN_ACTIVE_ONLY_STORAGE_KEY = 'groupchat:bcn-active-only';

/** 群策略标签 */
export const GROUP_STRATEGY_LABEL: Record<GroupStrategy, string> = {
  chat: '自由聊天-讨论模式',
  manager_worker: '任务协作-主从模式',
  state_machine: '自定义协作',
};

/** 群策略色点 */
export const GROUP_STRATEGY_DOT: Record<GroupStrategy, string> = {
  chat: 'bg-emerald-400',
  manager_worker: 'bg-violet-400',
  state_machine: 'bg-emerald-400',
};

export const CUSTOM_COLLAB_HUMAN_INVITE_DISABLED_TIP =
  '目前自定义协同暂时不支持邀请人类加入';

export const MANAGER_WORKER_ENGINE_SUPPORT_TIP =
  '目前只有个人 OpenClaw 引擎 Bot 和桌面 OpenClaw 引擎 Bot 支持主从模式。其余类型支持进行中';

export const MANAGER_WORKER_DEFAULT_WORKER_TIP =
  '其他 Bot 默认为从节点（Worker Bot），路由规则由主节点自行控制';

export const isHumanInviteDisabledForGroupStrategy = (
  groupStrategy?: GroupStrategy,
) => groupStrategy === 'state_machine';

export const getBcnActiveOnlyPreference = () => {
  try {
    return localStorage.getItem(BCN_ACTIVE_ONLY_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
};

export const setBcnActiveOnlyPreference = (activeOnly: boolean) => {
  try {
    localStorage.setItem(
      BCN_ACTIVE_ONLY_STORAGE_KEY,
      activeOnly ? 'true' : 'false',
    );
  } catch {
    // 忽略 localStorage 不可用场景。
  }
};
