// 编译期类型契约 + barrel 导出冒烟。
// Equal<...> 契约在 npm run typecheck 生效(类型变化时 Equal 变 false,编译失败)。
import type {
  ConversationBotSection,
  ConversationFriendDirectoryState,
  ConversationFriendGroupView,
  ConversationOrigin,
  ConversationRouteState,
  ConversationSessionListState,
  ConversationSessionScope,
  ConversationUserView,
  ManagedBotConversationView,
} from '@/domain/conversation';
import * as conversationDomain from '@/domain/conversation';
import { describe, expect, it } from '@jest/globals';

type Equal<X, Y> = (<T>() => T extends X ? 1 : 2) extends <T>() => T extends Y ? 1 : 2 ? true : false;

type RouteStateFields = 'botId' | 'section' | 'origin' | 'scope' | 'friendUserId' | 'sessionId';

/** 空 Session 列表状态(未加载/失败等场景的合法取值)。 */
const emptySessionList: ConversationSessionListState = {
  items: [],
  page: 1,
  total: 0,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
};

describe('domain/conversation types', () => {
  it('exports the route contract functions from the barrel', () => {
    expect(typeof conversationDomain.parseConversationRoute).toBe('function');
    expect(typeof conversationDomain.serializeConversationRoute).toBe('function');
  });

  it('type contracts hold at compile time', () => {
    const originEnum: Equal<ConversationOrigin, 'mine' | 'others'> = true;
    const scopeEnum: Equal<ConversationSessionScope, 'all' | 'favorite'> = true;
    const sectionEnum: Equal<ConversationBotSection, 'managed' | 'team' | 'friend'> = true;
    const friendGroupState: Equal<ConversationFriendGroupView['state'], 'unloaded' | 'loading' | 'loaded' | 'error'> =
      true;
    const routeStateAllOptional: Equal<
      ConversationRouteState,
      Partial<Pick<ConversationRouteState, RouteStateFields>>
    > = true;
    const userViewShape: Equal<ConversationUserView, { userId: string; displayName: string }> = true;
    const directoryShape: Equal<
      ConversationFriendDirectoryState,
      { items: ConversationUserView[]; loading: boolean; error: string | null }
    > = true;
    const managedViewShape: Equal<
      Pick<ManagedBotConversationView, 'botId' | 'origin' | 'scope' | 'sessions' | 'friendDirectory' | 'friendGroups'>,
      ManagedBotConversationView
    > = true;
    expect([
      originEnum,
      scopeEnum,
      sectionEnum,
      friendGroupState,
      routeStateAllOptional,
      userViewShape,
      directoryShape,
      managedViewShape,
    ]).not.toContain(false);
  });

  it('route and view values match the documented shapes', () => {
    const route: ConversationRouteState = {
      botId: 'bot-a:2088',
      section: 'managed',
      origin: 'others',
      scope: 'all',
      friendUserId: '447147',
      sessionId: 's1',
    };
    const user: ConversationUserView = { userId: '447147', displayName: '张三' };
    const directory: ConversationFriendDirectoryState = { items: [user], loading: false, error: null };
    const managed: ManagedBotConversationView = {
      botId: 'bot-a:2088',
      origin: 'mine',
      scope: 'all',
      sessions: emptySessionList,
      friendDirectory: directory,
      friendGroups: {},
    };
    const group: ConversationFriendGroupView = {
      friend: user,
      state: 'unloaded',
      sessions: emptySessionList,
      expanded: false,
    };
    expect(route).toEqual({
      botId: 'bot-a:2088',
      section: 'managed',
      origin: 'others',
      scope: 'all',
      friendUserId: '447147',
      sessionId: 's1',
    });
    expect(managed.friendGroups).toEqual({});
    expect(group.friend.displayName).toBe('张三');
  });
});
