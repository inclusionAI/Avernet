// Conversation Store 的纯切片 helpers:从 conversationStore.ts 拆出(Store 文件大小阈值),
// 只做状态 patch 计算且无副作用;被 Store 的同步 setter 组合使用,不对外导出消费。
import type { ConversationState } from './conversationStoreState';

/** 删除记录键并返回新对象(不可变切片;原对象不动)。 */
export function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

/** 过滤条件实际变化的公共落地:该 Bot 的 revision +1(代次供 Hook 拼请求身份与写回校验)。 */
export function revisionPatch(
  state: ConversationState,
  botId: string,
): Pick<ConversationState, 'sessionFilterRevisionByBotId'> {
  return {
    sessionFilterRevisionByBotId: {
      ...state.sessionFilterRevisionByBotId,
      [botId]: (state.sessionFilterRevisionByBotId[botId] ?? 0) + 1,
    },
  };
}

/** sessionKey 清除切片(favorite 入口清搜索共用);无记录时不产生变化。 */
export function clearSessionKeyPatch(state: ConversationState, botId: string) {
  return state.sessionKeyByBotId[botId] !== undefined
    ? { sessionKeyByBotId: withoutKey(state.sessionKeyByBotId, botId) }
    : {};
}

/** sessionKey 写入切片:trim 后非空落记录,空条件删除记录(空记录=未设置)。 */
export function sessionKeyPatch(state: ConversationState, botId: string, nextKey: string) {
  return nextKey
    ? { sessionKeyByBotId: { ...state.sessionKeyByBotId, [botId]: nextKey } }
    : clearSessionKeyPatch(state, botId);
}

/** 实例范围选择写入切片:非空落记录,空串删除记录(空记录=未限定实例)。 */
export function instanceSelectionPatch(state: ConversationState, botId: string, instanceUuid: string) {
  return instanceUuid
    ? { instanceSelectionByBotId: { ...state.instanceSelectionByBotId, [botId]: instanceUuid } }
    : state.instanceSelectionByBotId[botId] !== undefined
    ? { instanceSelectionByBotId: withoutKey(state.instanceSelectionByBotId, botId) }
    : {};
}

/** 列表缓存失效切片:删除该 Bot 的管理/好友列表缓存,由 Hook 按当前条件重新拉取;
 *  展开意图(expandedFriendUserIds)与主会话选中不属于缓存,保持不动。 */
export function invalidateListCaches(state: ConversationState, botId: string) {
  return {
    ...(state.sessionsByBotId[botId] !== undefined
      ? { sessionsByBotId: withoutKey(state.sessionsByBotId, botId) }
      : {}),
    ...(state.friendBotSessionsByBotId[botId] !== undefined
      ? { friendBotSessionsByBotId: withoutKey(state.friendBotSessionsByBotId, botId) }
      : {}),
  };
}

/** 好友 Bot 列表缓存失效切片(好友范围切换只影响自己的列表)。 */
export function invalidateFriendBotList(state: ConversationState, botId: string) {
  return state.friendBotSessionsByBotId[botId] !== undefined
    ? { friendBotSessionsByBotId: withoutKey(state.friendBotSessionsByBotId, botId) }
    : {};
}

/** 实例选择提交切片:等值提交为 no-op;实际变化与 sessionKey 条件同语义——
 *  列表缓存失效 + 过滤代次递增(在途旧实例响应不可写回,由 Hook 按新范围重拉)。 */
export function instanceSelectionSet(state: ConversationState, botId: string, next: string) {
  if ((state.instanceSelectionByBotId[botId] ?? '') === next) return state;
  return {
    ...instanceSelectionPatch(state, botId, next),
    ...invalidateListCaches(state, botId),
    ...revisionPatch(state, botId),
  };
}