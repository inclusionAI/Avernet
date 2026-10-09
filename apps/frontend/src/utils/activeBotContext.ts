/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * activeBotContext - 当前激活 Bot 上下文工具
 *
 * 用于非 React 环境（如 Controller 层）从全局 store 读取当前 Bot 信息，
 * 配合协作者权限模型，向受影响的接口注入 owner_id 兜底。
 *
 * 既有模式参考：src/services/backend-api/ResourceController.ts 的 getActiveBotId()
 */

import * as ServiceBotController from '@/services/backend-api/ServiceBotController';
import { USE_SERVICE_BOT_COLLAB_MOCK as USE_MOCK } from '@/services/mock';
import * as ServiceBotMock from '@/services/mock/ServiceBotController';
import { useBotStore, type Bot } from '@/stores/botStore';
import { useServiceBotCollabStore } from '@/stores/serviceBotCollabStore';
import { useUserStore } from '@/stores/userStore';
import { toast } from 'sonner';

/**
 * 获取当前激活的 Bot 对象
 * 同时检查 personal bots 和 allBots（含 service bots）
 */
export function getActiveBot(): Bot | undefined {
  const { activeBotId, bots, allBots } = useBotStore.getState();
  if (!activeBotId) return undefined;
  return (
    bots.find((b) => b.bot_id === activeBotId) ||
    allBots.find((b) => b.bot_id === activeBotId)
  );
}

/**
 * 获取指定 botId 对应的 Bot 对象
 */
export function getBotById(botId: string | null | undefined): Bot | undefined {
  if (!botId) return undefined;
  const { bots, allBots } = useBotStore.getState();
  return (
    bots.find((b) => b.bot_id === botId) ||
    allBots.find((b) => b.bot_id === botId)
  );
}

/**
 * Bot 复合唯一标识：bot_id 不再唯一（多个 owner 的 default Bot 同名 bot_id），
 * 需 bot_id + owner_id 一起作为列表/下拉的唯一 key。
 */
const BOT_KEY_SEP = '::';

export function makeBotKey(
  botId: string | null | undefined,
  ownerId: string | null | undefined,
): string {
  return `${botId ?? ''}${BOT_KEY_SEP}${ownerId ?? ''}`;
}

export function parseBotKey(key: string): {
  botId: string;
  ownerId: string | null;
} {
  const idx = key.indexOf(BOT_KEY_SEP);
  if (idx < 0) return { botId: key, ownerId: null };
  const ownerId = key.slice(idx + BOT_KEY_SEP.length);
  return { botId: key.slice(0, idx), ownerId: ownerId || null };
}

/**
 * 按 bot_id + owner_id 精确匹配 Bot 对象（用于去重场景）
 */
export function getBotByKey(
  botId: string | null | undefined,
  ownerId: string | null | undefined,
): Bot | undefined {
  if (!botId) return undefined;
  const { bots, allBots } = useBotStore.getState();
  const match = (b: Bot) =>
    b.bot_id === botId && (!ownerId || b.owner_id === ownerId);
  return bots.find(match) || allBots.find(match);
}

/**
 * 获取当前激活的 bot_id（若无返回 null）
 */
export function getActiveBotId(): string | null {
  return useBotStore.getState().activeBotId;
}

/**
 * 获取需要注入到协作场景接口的 owner_id：
 * - 能从 bot 拿到 owner_id → 返回 bot.owner_id
 * - 没有激活 Bot 或拿不到信息 → 返回 null
 *
 * 注：owner 自己调和协作者调走同一条路径——后端用 bot_id + owner_id 定位 Bot 鉴权，
 * owner 自己调时 user_id == owner_id，鉴权结果一致。统一注入避免行为分裂。
 */
export function getActiveBotOwnerId(): string | null {
  const bot = getActiveBot();
  return bot?.owner_id ?? null;
}

/**
 * 计算 Skill 类接口（uploadSkill / updateSkill / deleteSkill 等）的 user_id 字段。
 *
 * 后端契约：skill 是 bot 维度资源，user_id 应当传 bot 的 owner（不是登录用户）。
 * 找不到 bot 或拿不到 owner_id → 退回 fallbackUserId（个人 bot / 新建场景）。
 *
 * 文档：内部接口文档 （skills 章节）
 */
export function resolveSkillOwnerUserId(
  botId: string | null | undefined,
  fallbackUserId: string,
): string {
  if (!botId) return fallbackUserId;
  const bot = getBotById(botId);
  return bot?.owner_id || fallbackUserId;
}

/**
 * 计算需要传给后端的 entity_id（bot 维度资源）。
 *
 * 后端契约：协作场景下接口涉及 entity_id 时，应当传 bot.entity_id（不是登录用户的 staffNo）。
 * 找不到 bot 时 → 退回 fallback（个人 bot / 新建场景）；fallback 为空时返回 undefined 让调用方决定。
 *
 * 文档：内部接口文档
 */
export function resolveBotEntityId(
  botId: string | null | undefined,
  fallback?: string,
): string | undefined {
  if (!botId) return fallback;
  const bot = getBotById(botId);
  return bot?.entity_id || fallback;
}

/**
 * 计算需要传给 Bot 系列接口的 owner_id。
 *
 * 后端契约：协作场景下 Bot API（update/delete/restart/...）的 owner_id query 参数
 * 用于按 Bot 归属鉴权——Owner 自己调时等同于不传；协作者调时必须传 bot.owner_id。
 *
 * 找不到 bot（启动期 / bot 还没进 store）→ 返回 undefined，调用方拼参数时直接省略，
 * 由后端按当前登录用户兜底（保持与历史 Controller pickOwnerId 行为一致）。
 *
 * 文档：内部接口文档
 */
export function resolveBotOwnerId(
  botId: string | null | undefined,
): string | undefined {
  if (!botId) return undefined;
  return getBotById(botId)?.owner_id ?? undefined;
}

// ======================== 协作锁前置校验 ========================

export interface AssertWriteAccessOptions {
  /** 操作描述（用于 toast 提示），如「创建任务」「删除技能」 */
  action?: string;
  /** 校验通过/失败的 toast 是否静默，默认 false（即默认提示） */
  silent?: boolean;
}

/**
 * 服务 Bot 写操作前置锁校验。
 *
 * 使用场景：在「任务列表」「Skill 我的/市场」等页面对一个**服务 Bot**做写操作前，
 * 必须先校验是否持锁。
 *
 * 调用方式：
 * ```ts
 * const ok = await assertBotWriteAccess(task.bot_id);
 * if (!ok) return;  // 已 toast，调用方直接 return
 * // 继续执行写操作
 * ```
 *
 * 通过规则（任一满足）：
 * - 该 Bot 不是服务 Bot
 * - 该 Bot 找不到（默认放行，留给后端鉴权）
 * - 当前用户持锁（无论是 Owner 还是协作者）
 *
 * 注意：协作锁与角色无关——Owner 也必须先抢锁才能写，
 * 否则会出现 Owner 旁路 / 与 useServiceBotCollab.canWrite（持锁才可写）行为分裂。
 *
 * 失败时：
 * - 静默模式不弹 toast，仅返回 false
 * - 默认弹 toast 提示「请先在 Bot 列表中点击「抢锁」获取协作锁」
 *
 * 锁状态查询策略：
 * 1. 优先用 serviceBotCollabStore 内存缓存（如果当前正在该 Bot 的 PermissionTab 中，已有缓存）
 * 2. 否则调用 getLockInfo 实时查询
 */
export async function assertBotWriteAccess(
  botId: string | null | undefined,
  options: AssertWriteAccessOptions = {},
): Promise<boolean> {
  const { action, silent = false } = options;

  if (!botId) return true; // 没有 botId 不强制校验，留给后端

  const bot = getBotById(botId);
  if (!bot) return true; // 找不到 bot 不在这一层挡，由后端鉴权
  if (bot.bot_type !== 'service') return true;

  const currentUserId = useUserStore.getState().userId;
  if (!currentUserId) return true;

  // 协作锁与角色无关：Owner 也必须持锁
  const ownerId = bot.owner_id;
  if (!ownerId) return true;

  // 1) 先用 store 内存缓存
  const collabState = useServiceBotCollabStore.getState();
  let lockHolderUserId: string | null = null;
  let lockHolderName: string | null = null;
  let needLock = true;
  if (
    collabState.currentBotId === botId &&
    collabState.currentOwnerId === ownerId
  ) {
    lockHolderUserId = collabState.lockInfo?.holder_user_id ?? null;
    lockHolderName = collabState.lockInfo?.holder_name ?? null;
    needLock = collabState.needLock;
  } else {
    // 2) 调接口实时查询
    try {
      const res = USE_MOCK
        ? await ServiceBotMock.getLockInfoMock(botId, ownerId)
        : await ServiceBotController.getLockInfo({
            bot_id: botId,
            owner_id: ownerId,
          });
      lockHolderUserId = res.data?.lock?.holder_user_id ?? null;
      lockHolderName = res.data?.lock?.holder_name ?? null;
      needLock = res.data?.need_lock ?? true;
    } catch (err) {
      console.error('[assertBotWriteAccess] getLockInfo error:', err);
      if (!silent) {
        toast.error('锁状态查询失败，请稍后重试');
      }
      return false;
    }
  }

  // 无协作者场景：owner 独占编辑，不需要锁
  if (!needLock) return true;

  if (lockHolderUserId === currentUserId) return true;

  // 不持锁，阻止
  if (!silent) {
    if (lockHolderUserId) {
      const holder = lockHolderName || lockHolderUserId;
      toast.error(
        action
          ? `无法${action}：当前由 ${holder} 持锁，请在服务 Bot 列表抢锁后再操作`
          : `当前由 ${holder} 持锁，请在服务 Bot 列表抢锁后再操作`,
      );
    } else {
      toast.error(
        action
          ? `无法${action}：请先在服务 Bot 列表点击「抢锁」`
          : '请先在服务 Bot 列表点击「抢锁」',
      );
    }
  }
  return false;
}
