// 管理 Bot「查看他人对话」请求模块(从 useManagedBotOthers 拆出的请求生命周期,文件大小阈值)。
// 并发保护:{botId, origin} 一代 generation + 请求身份元组(含 sessionKey/过滤代次)隔离,
// 用户切换整批作废(epoch 前移 + abort 目录请求);写回前校验 epoch/generation/代次/当前
// origin,陈旧响应互不覆盖。请求完成只清理自己的在途标记,旧请求不清洗新请求的身份
// (在途标记按 token 认领:force 重试覆盖同 key 旧标记时,旧请求完成后不得清洗新请求)。
import type { ConversationBotView } from '@/domain/conversation/types';
import { BOT_SESSION_PAGE_SIZE } from '@/services/workspace/botSessionService';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { useConversationStore } from '@/stores/conversationStore';
import {
  emptySessions,
  filterRevisionOf,
  groupOutcomeOf,
  patchOthersView,
  sessionKeyOf,
  writeOthersGroup,
} from './conversationSessionCache';

/** others 请求上下文:epoch/代次/在途 token/abort 控制器,由 Hook 挂载一份(useRef 持有)。 */
export interface OthersRequestContext {
  /** 当前请求用户(Hook 渲染期同步写入)。 */
  userId: { current: string | null };
  /** 用户代次:用户切换/卸载 +1,配合 abort 作废全部在途写入。 */
  epoch: { current: number };
  /** 请求代次,按保护键(目录/每好友)一 Map。 */
  generations: Map<string, number>;
  /** 在途请求 token 认领(同 key 重试不互清洗)。 */
  inFlights: Map<string, symbol>;
  /** 目录请求的 AbortController(key 同在途)。 */
  controllers: Map<string, AbortController>;
}

export function createOthersRequestContext(userId: string | null): OthersRequestContext {
  return {
    userId: { current: userId },
    epoch: { current: 0 },
    generations: new Map<string, number>(),
    inFlights: new Map<string, symbol>(),
    controllers: new Map<string, AbortController>(),
  };
}

/** abort 全部目录在途请求并清空控制器表(用户切换/卸载调用)。 */
export function abortOthersRequests(ctx: OthersRequestContext): void {
  for (const controller of ctx.controllers.values()) controller.abort();
  ctx.controllers.clear();
}

export function othersBeginFlight(ctx: OthersRequestContext, key: string): symbol {
  const token = Symbol(key);
  ctx.inFlights.set(key, token);
  return token;
}

export function othersEndFlight(ctx: OthersRequestContext, key: string, token: symbol): void {
  if (ctx.inFlights.get(key) === token) ctx.inFlights.delete(key);
}

/** 递增保护键代次并返回新代次(旧响应写回时按代次比对即丢弃)。 */
export function othersNextGeneration(ctx: OthersRequestContext, key: string): number {
  const next = (ctx.generations.get(key) ?? 0) + 1;
  ctx.generations.set(key, next);
  return next;
}

/** 写回前的共同校验:epoch、代次、过滤代次、当前 origin 是否仍是 others。 */
export function canWriteOthers(
  ctx: OthersRequestContext,
  botId: string,
  genKey: string,
  generation: number,
  epoch: number,
  revision: number,
): boolean {
  const store = useConversationStore.getState();
  return (
    epoch === ctx.epoch.current &&
    ctx.generations.get(genKey) === generation &&
    filterRevisionOf(botId, store) === revision &&
    (store.originByManagedBotId[botId] ?? 'mine') === 'others'
  );
}

/** 好友目录首拉(force 供 retry 用):身份含过滤代次,目录按新条件重拉不受旧请求阻塞。 */
export function loadOthersDirectory(ctx: OthersRequestContext, view: ConversationBotView, force: boolean): void {
  const botId = view.bot.botId;
  if (!ctx.userId.current) return;
  const store = useConversationStore.getState();
  // 目录数据不随 key 变化,但身份须含过滤代次:key 变化删除缓存重拉目录,旧请求不得阻塞/写回。
  const revision = filterRevisionOf(botId, store);
  const requestKey = JSON.stringify(['others-directory', botId, revision]);
  if (ctx.inFlights.has(requestKey)) return;
  const cached = store.sessionsByBotId[botId];
  // 已成功(含确认空列表)不自动拉;失败等待 retryDirectory,避免反应式 effect 循环。
  const loadedOnce = cached?.origin === 'others' && !cached.friendDirectory.loading && !cached.friendDirectory.error;
  if (!force && loadedOnce) return;
  const genKey = `${botId}|others`;
  const generation = othersNextGeneration(ctx, genKey);
  const epoch = ctx.epoch.current;
  const inFlight = othersBeginFlight(ctx, requestKey);
  patchOthersView(botId, (item) => ({
    ...item,
    origin: 'others',
    scope: 'all',
    friendDirectory: { ...item.friendDirectory, loading: true, error: null },
  }));
  const controller = new AbortController();
  ctx.controllers.set(requestKey, controller);
  void managedBotConversationService.loadFriendUsers(view.bot, controller.signal).then((result) => {
    ctx.controllers.delete(requestKey);
    othersEndFlight(ctx, requestKey, inFlight);
    if (!canWriteOthers(ctx, botId, genKey, generation, epoch, revision)) return;
    // 已存在的分组保留会话/加载/展开状态并补写权威好友资料,只对新增好友补充空分组。
    patchOthersView(botId, (item) => {
      if (!result.ok) {
        return {
          ...item,
          friendDirectory: { ...item.friendDirectory, loading: false, error: result.error.friendlyMessage },
        };
      }
      const friendGroups = { ...item.friendGroups };
      for (const user of result.data) {
        // 目录是好友资料的权威来源:分组先于目录建好(过滤变化触发的同轮重拉会以
        // userId 兜底)时,在此补写真实资料;分组的会话/加载/展开状态原样保留。
        const existing = friendGroups[user.userId];
        friendGroups[user.userId] = existing
          ? { ...existing, friend: user }
          : {
              friend: user,
              state: 'unloaded',
              sessions: emptySessions(),
              expanded: false,
            };
      }
      return { ...item, friendDirectory: { items: result.data, loading: false, error: null }, friendGroups };
    });
  });
}

/** 好友会话首拉;force=true 供 retry 用(作废同 key 旧请求,直接重发)。 */
export function loadOthersFriendSessions(
  ctx: OthersRequestContext,
  view: ConversationBotView,
  friendUserId: string,
  force: boolean,
): void {
  const botId = view.bot.botId;
  if (!ctx.userId.current) return;
  const store = useConversationStore.getState();
  const group = store.sessionsByBotId[botId]?.friendGroups[friendUserId];
  const sessionKey = sessionKeyOf(botId, store);
  const revision = filterRevisionOf(botId, store);
  const requestKey = JSON.stringify(['others-friend-sessions', botId, friendUserId, sessionKey, revision]);
  if (!force) {
    if (ctx.inFlights.has(requestKey)) return;
    // 失败分组等待 retryFriendSessions,避免反应式 effect 循环重试。
    if (group && (group.state === 'loaded' || group.state === 'error')) return;
  }
  const genKey = `fs|${botId}|${friendUserId}`;
  const generation = othersNextGeneration(ctx, genKey);
  const epoch = ctx.epoch.current;
  const inFlight = othersBeginFlight(ctx, requestKey);
  patchOthersView(botId, (item) => ({
    ...item,
    origin: 'others',
    scope: 'all',
    friendGroups: {
      ...item.friendGroups,
      [friendUserId]: {
        friend: group?.friend ??
          item.friendDirectory.items.find((user) => user.userId === friendUserId) ?? {
            userId: friendUserId,
            displayName: friendUserId,
          },
        state: 'loading',
        expanded: useConversationStore.getState().expandedFriendUserIdsByBotId[botId]?.[friendUserId] === true,
        sessions: {
          ...(group?.sessions ?? emptySessions()),
          loading: true,
          error: null,
        },
      },
    },
  }));
  void managedBotConversationService
    .listOtherSessions(view.bot, friendUserId, 1, BOT_SESSION_PAGE_SIZE, sessionKey)
    .then((result) => {
      othersEndFlight(ctx, requestKey, inFlight);
      if (!canWriteOthers(ctx, botId, genKey, generation, epoch, revision)) return;
      const expandedStill =
        useConversationStore.getState().expandedFriendUserIdsByBotId[botId]?.[friendUserId] === true;
      writeOthersGroup(botId, friendUserId, (groupNow) => ({
        ...groupNow,
        expanded: expandedStill,
        ...groupOutcomeOf(result, groupNow.sessions),
      }));
    });
}