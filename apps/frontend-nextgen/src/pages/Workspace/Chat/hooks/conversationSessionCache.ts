// 会话列表缓存态的纯构建/写回助手:被 useConversationSessions 与 useManagedBotOthers 共用。
// 只做 Store 同步写回与状态拼装,不含请求生命周期(generation/abort 留在各 Hook)。
import type {
  ConversationBotSection,
  ConversationFriendDirectoryState,
  ConversationFriendGroupView,
  ConversationSessionListState,
  ConversationSessionScope,
  ManagedBotConversationView,
} from '@/domain/conversation/types';
import { isManagedConversationSection } from '@/domain/conversation/types';
import type { BotSessionPageView } from '@/services/workspace/botSessionService';
import { BOT_SESSION_PAGE_SIZE } from '@/services/workspace/botSessionService';
import type { DomainResult } from '@/services/workspace/identityService';
import type { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { useConversationStore } from '@/stores/conversationStore';

const EMPTY_FIELDS = {
  page: 1,
  total: 0,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
};

export const emptySessions = (): ConversationSessionListState => ({ ...EMPTY_FIELDS, items: [] });

/** 首页列表态。 */
export const firstPageOf = (
  items: ConversationSessionListState['items'],
  total: number,
): ConversationSessionListState => ({ ...emptySessions(), items, total, hasMore: BOT_SESSION_PAGE_SIZE < total });

/** 追加下一页；使用最新缓存，重取偏移页时去重并保留已更新的会话状态。 */
export const nextPageOf = (
  base: ConversationSessionListState,
  result: DomainResult<BotSessionPageView>,
  page: number,
): ConversationSessionListState => {
  if (!result.ok) return { ...base, isLoadingMore: false, loadMoreError: result.error.friendlyMessage };
  return {
    ...base,
    items: [
      ...base.items,
      ...result.data.items.filter((item) => !base.items.some((existing) => existing.sessionId === item.sessionId)),
    ],
    page,
    total: result.data.total,
    hasMore: page * BOT_SESSION_PAGE_SIZE < result.data.total,
    loading: false,
    error: null,
    isLoadingMore: false,
    loadMoreError: null,
  };
};

/** 管理 Bot 的 mine 视图包装:保留 others 视角遗留的 friendDirectory/friendGroups。 */
export const managedViewOf = (
  botId: string,
  scope: ConversationSessionScope,
  prev: ManagedBotConversationView | undefined,
  sessions: ConversationSessionListState,
): ManagedBotConversationView => ({
  botId,
  origin: 'mine',
  scope,
  sessions,
  friendDirectory: prev?.friendDirectory ?? { items: [], loading: false, error: null },
  friendGroups: prev?.friendGroups ?? {},
});

/** 会话列表统一写回(managed → 视图缓存;friend → 好友 Bot 列表缓存)。 */
export const writeSessionState = (
  botId: string,
  section: ConversationBotSection,
  scope: ConversationSessionScope,
  state: ConversationSessionListState,
): void => {
  const store = useConversationStore.getState();
  if (isManagedConversationSection(section))
    store.setManagedBotCache(botId, managedViewOf(botId, scope, store.sessionsByBotId[botId], state));
  else store.setFriendBotSessions(botId, state);
};

export const requestKeyOf = (
  botId: string,
  section: ConversationBotSection,
  scope: ConversationSessionScope,
  more: boolean,
): string => `${section}:${botId}:${scope}:${more ? 'more' : 'page'}`;

// —— 管理 Bot others(origin=others)视角的缓存切片助手 ——

const EMPTY_DIRECTORY: ConversationFriendDirectoryState = { items: [], loading: false, error: null };

export const othersViewOf = (prev: ManagedBotConversationView | undefined, botId: string): ManagedBotConversationView =>
  prev ?? {
    botId,
    origin: 'others',
    scope: 'all',
    friendDirectory: EMPTY_DIRECTORY,
    friendGroups: {},
    sessions: emptySessions(),
  };

export const patchOthersView = (
  botId: string,
  rebuild: (view: ManagedBotConversationView) => ManagedBotConversationView,
): void => {
  const store = useConversationStore.getState();
  store.setManagedBotCache(botId, rebuild(othersViewOf(store.sessionsByBotId[botId], botId)));
};

/** 只读会话结果 → 好友分组切片;失败保留既有列表并落到 error 态。 */
export const groupOutcomeOf = (
  result: Awaited<ReturnType<typeof managedBotConversationService.listOtherSessions>>,
  prevSessions: ConversationSessionListState,
): Pick<ManagedBotConversationView['friendGroups'][string], 'state' | 'sessions' | 'error'> =>
  result.ok
    ? { state: 'loaded', sessions: result.data, error: undefined }
    : {
        state: 'error',
        error: result.error.friendlyMessage,
        sessions: { ...prevSessions, loading: false, isLoadingMore: false, error: result.error.friendlyMessage },
      };

/** 只写某个好友分组(分组不存在则不写)。 */
export const writeOthersGroup = (
  botId: string,
  friendUserId: string,
  patch: (group: ConversationFriendGroupView) => ConversationFriendGroupView,
): void => {
  patchOthersView(botId, (item) => {
    const groupNow = item.friendGroups[friendUserId];
    return groupNow ? { ...item, friendGroups: { ...item.friendGroups, [friendUserId]: patch(groupNow) } } : item;
  });
};

/** 收藏移除会使后端 offset 前移；不足整页时重取该页补齐，而非跳到下一页。 */
export const sessionRequestPage = (
  state: ConversationSessionListState | undefined,
  scope: ConversationSessionScope,
  more: boolean,
): number =>
  !more
    ? 1
    : scope === 'favorite'
    ? Math.floor((state?.items.length ?? 0) / BOT_SESSION_PAGE_SIZE) + 1
    : (state?.page ?? 1) + 1;
