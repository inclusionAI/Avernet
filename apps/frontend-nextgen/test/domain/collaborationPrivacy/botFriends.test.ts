// 好友名单（公开设置）纯逻辑：客户端搜索 + 分类计数派生。搜索覆盖完整拉取
// 集合（服务端分页已由 fetch-all 展开）,按展示名或原始 actor id（staff_no /
// bot uuid)大小写不敏感子串匹配；计数对每个类型独立降级。
import {
  filterFriendViewsByKeyword,
  rosterCounts,
} from '@/domain/collaborationPrivacy/botFriends';
import type {
  BotFriendPage,
  BotIdentityFriendView,
} from '@/services/workspace/botFriendConversationService';
import type { DomainResult } from '@/services/workspace/identityService';
import { describe, expect, it } from '@jest/globals';

const friendView = (overrides: Partial<BotIdentityFriendView> = {}): BotIdentityFriendView => ({
  actorType: 'human',
  actorId: '447147',
  queryId: 'human_447147',
  displayName: '风太',
  online: true,
  detailsResolved: true,
  ...overrides,
});

const sectionOk = (
  items: BotIdentityFriendView[],
  total = items.length,
): DomainResult<BotFriendPage<BotIdentityFriendView>> => ({
  ok: true,
  data: { items, total, page: 1, pageSize: 100, hasMore: false },
});

const sectionFailure = (code: string): DomainResult<BotFriendPage<BotIdentityFriendView>> => ({
  ok: false,
  error: { code, friendlyMessage: '加载失败', canRetry: true },
});

const VIEWS: BotIdentityFriendView[] = [
  friendView(),
  friendView({
    actorType: 'human',
    actorId: '448148',
    queryId: 'human_448148',
    displayName: '雨太',
  }),
  friendView({
    actorType: 'bot',
    actorId: 'bot-expert:100',
    queryId: 'bot-expert:100',
    displayName: '本地代码专家',
    detailsResolved: true,
  }),
  friendView({
    actorType: 'bot',
    actorId: 'bot-dangling:9',
    queryId: 'bot-dangling:9',
    displayName: 'bot-dangling:9',
    online: false,
    detailsResolved: false,
  }),
];

describe('filterFriendViewsByKeyword', () => {
  it('matches a display name', () => {
    expect(filterFriendViewsByKeyword(VIEWS, '风太')).toEqual([VIEWS[0]]);
    expect(filterFriendViewsByKeyword(VIEWS, '代码专家')).toEqual([VIEWS[2]]);
  });

  it('matches a raw staff no and a raw bot uuid', () => {
    expect(filterFriendViewsByKeyword(VIEWS, '448148')).toEqual([VIEWS[1]]);
    expect(filterFriendViewsByKeyword(VIEWS, 'bot-expert')).toEqual([VIEWS[2]]);
  });

  it('is case-insensitive over both display names and actor ids', () => {
    expect(filterFriendViewsByKeyword(VIEWS, 'BOT-EXPERT')).toEqual([VIEWS[2]]);
    expect(filterFriendViewsByKeyword(VIEWS, 'bot-DANGLING')).toEqual([VIEWS[3]]);
  });

  it('trims the keyword and returns everything for an empty one', () => {
    expect(filterFriendViewsByKeyword(VIEWS, '  风太  ')).toEqual([VIEWS[0]]);
    expect(filterFriendViewsByKeyword(VIEWS, '')).toBe(VIEWS);
    expect(filterFriendViewsByKeyword(VIEWS, '   ')).toBe(VIEWS);
  });

  it('returns an empty list when nothing matches', () => {
    expect(filterFriendViewsByKeyword(VIEWS, '不存在的人')).toEqual([]);
  });
});

describe('rosterCounts', () => {
  it('reads the totals of both successful sections', () => {
    expect(rosterCounts(sectionOk(VIEWS.slice(0, 2)), sectionOk(VIEWS.slice(2)))).toEqual({
      users: 2,
      bots: 2,
    });
  });

  it('degrades each failed side independently to null', () => {
    expect(rosterCounts(sectionOk(VIEWS.slice(0, 2)), sectionFailure('BOTS_FAIL'))).toEqual({
      users: 2,
      bots: null,
    });
    expect(rosterCounts(sectionFailure('USERS_FAIL'), sectionOk(VIEWS.slice(2)))).toEqual({
      users: null,
      bots: 2,
    });
    expect(rosterCounts(sectionFailure('USERS_FAIL'), sectionFailure('BOTS_FAIL'))).toEqual({
      users: null,
      bots: null,
    });
  });
});
