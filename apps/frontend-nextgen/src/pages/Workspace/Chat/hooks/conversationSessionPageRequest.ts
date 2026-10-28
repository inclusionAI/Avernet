// 会话分页请求模块(从 useConversationSessions 拆出的请求生命周期,文件大小阈值):
// 并发代次 + 请求去重 + 过滤身份校验 + 列表写回。与 conversationSessionCache 同为
// 无状态纯函数模块,由 Hook 用 useRef 持有一份请求上下文装配(行内语义不变)。
import { getCapabilities } from '@/capabilities';
import type {
  ConversationBotSection,
  ConversationBotView,
  ConversationSessionRequestRange,
  ConversationSessionScope,
} from '@/domain/conversation/types';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { BOT_SESSION_PAGE_SIZE } from '@/services/workspace/botSessionService';
import { conversationService } from '@/services/workspace/conversationService';
import { useConversationStore } from '@/stores/conversationStore';
import {
  filterRevisionOf,
  firstPageOf,
  friendScopeOf,
  nextPageOf,
  requestKeyOf,
  sessionKeyOf,
  sessionRequestPage,
  writeSessionState,
} from './conversationSessionCache';

/** 会话请求上下文:请求代次与在途去重,由 Hook 挂载一份(useRef 持有)。 */
export interface ConversationSessionRequestContext {
  /** 当前请求用户(Hook 渲染期同步写入)。 */
  userId: { current: string | null };
  /** 请求代次:用户切换/卸载 +1,在途旧响应整体作废。 */
  generation: { current: number };
  /** 在途请求 key 集合(同请求身份去重)。 */
  inFlight: Set<string>;
}

export function createSessionRequestContext(userId: string | null): ConversationSessionRequestContext {
  return { userId: { current: userId }, generation: { current: 0 }, inFlight: new Set<string>() };
}

/** 管理会话写回时读取上下文(origin/scope)须仍一致,不一致即丢弃。 */
export function stillMineOrigin(botId: string, scope: ConversationSessionScope): boolean {
  const store = useConversationStore.getState();
  return (
    (store.originByManagedBotId[botId] ?? 'mine') === 'mine' &&
    (store.effectiveScopeByManagedBotId[botId] ?? 'all') === scope
  );
}

/** 列表分页请求主体(首拉/加载更多共用):请求身份读取、缓存命中判定、条件一致性写回。 */
export function loadSessionPage(
  ctx: ConversationSessionRequestContext,
  isPending: (botId: string, section: ConversationBotSection) => boolean,
  view: ConversationBotView,
  more: boolean,
): void {
  const botId = view.bot.botId;
  const sectionOf: ConversationBotSection = view.section;
  const userIdNow = ctx.userId.current;
  if (!userIdNow || !view.bot.chatable || isPending(botId, sectionOf)) return;
  // 多实例服务 Bot 实例范围(capability 缺省 null → 零行为):首拉幂等触发实例清单加载
  // (服务 Bot 专用,其余 no-op);多实例解析后自动选中写入 Store(过滤代次+1)经既有
  // scopeSignature 触发重拉,此间在途的未带范围响应由 revision 写回校验自动丢弃。
  const instanceScope = getCapabilities().getConversationInstanceScope().value;
  if (!more) instanceScope?.ensureInstancesLoaded(view.bot);
  const requestRange = instanceScope?.getSessionRange(view.bot) ?? undefined;
  // 无范围时不追加调用参数:保持既有调用形状,全部既有用例零改动通过(开源版零行为变化)。
  const requestRangeArgs: [] | [ConversationSessionRequestRange] = requestRange ? [requestRange] : [];
  // 请求快照:从最新 Store 读取生效过滤(mine 范围/好友范围/sessionKey/代次)。
  const store = useConversationStore.getState();
  const scope = isManagedConversationSection(sectionOf)
    ? store.effectiveScopeByManagedBotId[botId] ?? 'all'
    : friendScopeOf(botId, store);
  const sessionKey = sessionKeyOf(botId, store);
  const revision = filterRevisionOf(botId, store);
  const managedCache = isManagedConversationSection(sectionOf) ? store.sessionsByBotId[botId] : undefined;
  const state = isManagedConversationSection(sectionOf)
    ? managedCache?.sessions
    : store.friendBotSessionsByBotId[botId];
  const requestKey = requestKeyOf(botId, sectionOf, 'mine', scope, sessionKey, revision, more);
  if (ctx.inFlight.has(requestKey)) return;
  if (more && (!state || !state.hasMore || state.loading || state.isLoadingMore)) return;
  const cachedHit = isManagedConversationSection(sectionOf)
    ? managedCache?.origin === 'mine' && managedCache?.scope === scope && !managedCache?.sessions.error
    : Boolean(state && !state.error);
  if (!more && cachedHit && !(state?.hasMore && state.items.length === 0)) return;
  if (!more && isManagedConversationSection(sectionOf) && (store.originByManagedBotId[botId] ?? 'mine') !== 'mine')
    return;
  ctx.inFlight.add(requestKey);
  const generation = ctx.generation.current;
  const page = sessionRequestPage(state, scope, more);
  // 首拉与缓存条件不一致(如 managed 范围切换)时不保留旧 items,以免把旧条件
  // 结果当成新列表展示;搜索/好友范围变化已由 Store 删除缓存(条件天然一致)。
  const criteriaMatch =
    more ||
    (isManagedConversationSection(sectionOf)
      ? managedCache?.origin === 'mine' && managedCache?.scope === scope
      : true);
  writeSessionState(botId, sectionOf, scope, {
    ...(criteriaMatch ? state ?? firstPageOf([], 0) : firstPageOf([], 0)),
    ...(more ? { isLoadingMore: true, loadMoreError: null } : { loading: true, error: null }),
  });
  void (
    isManagedConversationSection(sectionOf)
      ? conversationService.listManagedSessions(
          view.bot,
          userIdNow,
          scope,
          page,
          BOT_SESSION_PAGE_SIZE,
          sessionKey,
          ...requestRangeArgs,
        )
      : conversationService.listFriendBotSessions(
          view.bot,
          userIdNow,
          page,
          BOT_SESSION_PAGE_SIZE,
          scope,
          sessionKey,
          ...requestRangeArgs,
        )
  ).then((result) => {
    ctx.inFlight.delete(requestKey);
    if (ctx.generation.current !== generation) return;
    const latestStore = useConversationStore.getState();
    // 过滤代次变化(范围/归属/搜索,含 A→B→A)即丢弃:旧响应不得写回或复原已清除的过滤。
    if (filterRevisionOf(botId, latestStore) !== revision) return;
    if (isManagedConversationSection(sectionOf) && !stillMineOrigin(botId, scope)) return;
    const latest = isManagedConversationSection(sectionOf)
      ? latestStore.sessionsByBotId[botId]?.sessions
      : latestStore.friendBotSessionsByBotId[botId];
    if (!result.ok) {
      if (!more) {
        writeSessionState(botId, sectionOf, scope, { ...firstPageOf([], 0), error: result.error.friendlyMessage });
      } else if (latest) {
        writeSessionState(botId, sectionOf, scope, nextPageOf(latest, result, page));
      }
      return;
    }
    if (!more) {
      writeSessionState(botId, sectionOf, scope, firstPageOf(result.data.items, result.data.total));
      return;
    }
    if (latest) writeSessionState(botId, sectionOf, scope, nextPageOf(latest, result, page));
  });
}